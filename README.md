# Polymarket US API Demo Scripts

Python scripts demonstrating the [Polymarket US](https://polymarket.us) retail API and SDK. Covers public market data, date-based market filtering, odds calculation, LLM-powered probability analysis, authenticated trading, async concurrency, and real-time WebSocket streaming.

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **Polymarket US account** (for authenticated endpoints) — download the iOS app, create an account, and complete identity verification
- **API keys** (for authenticated endpoints) — generate at [polymarket.us/developer](https://polymarket.us/developer)
- **LLM API key** (for script 09) — at least one of: Anthropic, OpenAI, Kimi, or a compatible local endpoint
- **Brave Search API key** (optional, for script 09 web enrichment) — get one at [brave.com/search/api](https://brave.com/search/api/)

## Setup

```bash
# Clone and enter the project
cd polymarket-us-init

# Install dependencies (creates .venv automatically)
uv sync
```

### API Key Configuration

Scripts 04–07 require Polymarket authentication. Script 09 requires an LLM API key for the analysis step. All scripts use `python-dotenv` to auto-load credentials from a `.env` file.

**Recommended: `.env` file**

```bash
cp .env.example .env
# Edit .env with your actual keys
```

The `.env` file supports:

```
# Polymarket trading (scripts 04-07)
POLYMARKET_KEY_ID=your-key-id-uuid
POLYMARKET_SECRET_KEY=your-base64-encoded-ed25519-private-key

# Brave Search API — enriches deep research with live web results (script 09)
# Get your key at https://brave.com/search/api/
BRAVE_SEARCH_API_KEY=your-brave-search-api-key

# LLM providers for script 09 — only set the one(s) you use
ANTHROPIC_API_KEY=your-anthropic-api-key        # Claude (default)
OPENAI_API_KEY=your-openai-api-key              # OpenAI GPT models
KIMI_API_KEY=sk-kimi-your-key-here              # Kimi Code (kimi.com/code)
LLM_API_KEY=your-key                            # Custom OpenAI-compatible endpoint
LLM_BASE_URL=https://your-provider.com/v1       # Custom endpoint base URL
```

**Fallback: environment variables**

If you prefer not to use a `.env` file, set the variables directly in your shell:

**Windows (CMD):**
```cmd
set POLYMARKET_KEY_ID=your-key-id-uuid
set POLYMARKET_SECRET_KEY=your-base64-ed25519-private-key
```

**Windows (PowerShell):**
```powershell
$env:POLYMARKET_KEY_ID = "your-key-id-uuid"
$env:POLYMARKET_SECRET_KEY = "your-base64-ed25519-private-key"
```

**Linux / macOS:**
```bash
export POLYMARKET_KEY_ID="your-key-id-uuid"
export POLYMARKET_SECRET_KEY="your-base64-ed25519-private-key"
```

> Your private key is shown only once when generated. Store it securely and never commit it to version control.

## Scripts

### Public Endpoints (No Authentication Required)

#### `01_browse_markets.py` — Browse Events & Markets

List events and markets with pagination, filtering, and status selection.

```bash
uv run 01_browse_markets.py                        # Show 10 open events + 10 markets
uv run 01_browse_markets.py --limit 20             # Show 20 of each
uv run 01_browse_markets.py --events-only          # Only events
uv run 01_browse_markets.py --markets-only         # Only markets
uv run 01_browse_markets.py --status open          # Only open/active (default)
uv run 01_browse_markets.py --status closed        # Only closed/settled
uv run 01_browse_markets.py --status all           # All statuses
```

**SDK methods:** `client.events.list()`, `client.markets.list()`

---

#### `02_search_markets.py` — Search Markets

Full-text search across events and markets with a CLI query argument.

```bash
uv run 02_search_markets.py "bitcoin"
uv run 02_search_markets.py "presidential election"
uv run 02_search_markets.py "super bowl" --limit 5
```

**SDK methods:** `client.search.query()`

---

#### `03_orderbook_viewer.py` — Order Book & BBO

Fetch the full order book depth and best bid/offer for a specific market.

```bash
uv run 03_orderbook_viewer.py btc-100k-2025              # BBO + order book
uv run 03_orderbook_viewer.py btc-100k-2025 --depth 10   # Show 10 price levels
uv run 03_orderbook_viewer.py btc-100k-2025 --bbo-only   # Only best bid/offer
uv run 03_orderbook_viewer.py btc-100k-2025 --settlement  # Include settlement info
```

**SDK methods:** `client.markets.bbo()`, `client.markets.book()`, `client.markets.settlement()`

---

#### `08_markets_by_date.py` — Markets by Resolution Date + Keyword Search

Find markets that resolve on a specific date or within a date range, with an optional keyword filter.

```bash
# All open markets resolving on a specific date
uv run 08_markets_by_date.py --date 2025-03-01

# Combine date + keyword search
uv run 08_markets_by_date.py --date 2025-03-01 --search "bitcoin"

# Date range
uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31

# Range + keyword + more results + include all statuses
uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31 --search "NBA" --limit 20 --status all

# Debug with raw JSON
uv run 08_markets_by_date.py --date 2025-06-30 --search "election" --verbose
```

**SDK methods:** `client.markets.list()`, `client.search.query()`

**Arguments:**

| Flag | Description |
|------|-------------|
| `--date YYYY-MM-DD` | Exact resolution date |
| `--date-from YYYY-MM-DD` | Start of resolution date range (inclusive) |
| `--date-to YYYY-MM-DD` | End of resolution date range (inclusive) |
| `--search TEXT` | Keyword filter across question, description, category, slug, and parent event title |
| `--status` | `open` (default), `closed`, or `all` |
| `--limit N` | Max results to display (default: 10) |
| `--verbose` | Print raw JSON for the first matching market |

> Uses client-side date filtering with automatic pagination (fetches up to 20 pages). When `--search` is provided, routes through the search endpoint for richer results.

---

#### `09_odds_calculator.py` — Odds Calculator & LLM Probability Analysis

Fetches live market prices, converts them into every major odds format, and passes the data to an LLM for analysis. Supports three ways to identify a market (slug, keyword search, or resolution date in EST), multiple LLM providers, an optional 4-stage deep research pipeline powered by Brave Search web results, liquidity-based market sorting and filtering, a consolidated multi-market summary, and a run summary footer listing every agent and source used.

##### Quick start

```bash
# Odds table + single-pass Claude analysis (default)
uv run 09_odds_calculator.py btc-100k-2025

# Odds only — no LLM required
uv run 09_odds_calculator.py btc-100k-2025 --no-llm

# Keyword search — show top 5, analyse result #0
uv run 09_odds_calculator.py --search "NBA finals" --limit 5 --pick 0

# Date search — all markets resolving 2026-03-15 (Eastern Time), pick #0
uv run 09_odds_calculator.py --date 2026-03-15 --limit 10 --pick 0
```

##### Choosing an LLM provider

Use `--llm` to select a provider. Each provider reads its API key from the environment automatically.

| Provider | Flag | Env var | Default model |
|----------|------|---------|---------------|
| Claude (default) | `--llm claude` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| OpenAI | `--llm openai` | `OPENAI_API_KEY` | `gpt-4o` |
| Kimi Code | `--llm kimi` | `KIMI_API_KEY` | `kimi-for-coding` |
| Custom endpoint | `--llm custom` | `LLM_API_KEY` + `LLM_BASE_URL` | *(must set `--model`)* |

```bash
# Claude with a more powerful model
uv run 09_odds_calculator.py btc-100k-2025 --llm claude --model claude-opus-4-6

# OpenAI GPT-4o (uses OPENAI_API_KEY from .env)
uv run 09_odds_calculator.py btc-100k-2025 --llm openai

# OpenAI with a specific model
uv run 09_odds_calculator.py btc-100k-2025 --llm openai --model gpt-4o-mini

# Kimi Code (key: sk-kimi-..., get it at kimi.com/code)
uv run 09_odds_calculator.py btc-100k-2025 --llm kimi

# Local Ollama (OpenAI-compatible)
uv run 09_odds_calculator.py btc-100k-2025 --llm custom --model llama3.2 \
    --llm-base-url http://localhost:11434/v1 --llm-api-key ollama

# Groq (OpenAI-compatible)
uv run 09_odds_calculator.py btc-100k-2025 --llm custom --model llama-3.3-70b-versatile \
    --llm-base-url https://api.groq.com/openai/v1 --llm-api-key "$GROQ_API_KEY"

# Override API key inline without editing .env
uv run 09_odds_calculator.py btc-100k-2025 --llm openai --llm-api-key sk-...
```

##### Brave Search web enrichment

When `BRAVE_SEARCH_API_KEY` is set, the script queries the [Brave Search API](https://brave.com/search/api/) for live team statistics, recent news, and public metrics relevant to the market question. Results are injected into the LLM context before analysis so the model reasons from current data rather than training knowledge alone.

- **Deep research** (`--deep-research`): web search always runs as step 0.
- **Single-pass**: opt in with `--web-search`.
- If the key is missing or the search fails, analysis continues without web context.

##### Deep research pipeline (`--deep-research`)

Enables two web-search steps followed by 4 LLM stages, each feeding into the next:

| Step | Role | Output |
|------|------|--------|
| 0a — Web Search | Brave Search for live team/event stats and recent news | Web context block |
| 0b — Social Search | Brave Search for X/Twitter and Reddit sentiment signals | Social context block |
| 1 — Research | Investigates using web + social context + training knowledge; initial probability estimate | Research report |
| 2 — Critique | Stress-tests the research: gaps, biases, counter-arguments, alternative scenarios | Critique report |
| 3 — Rebuttal | Responds to critiques, concedes where valid, defends where not, revised estimate | Rebuttal report |
| 4 — Consolidation | Synthesises all findings into final probability + sentiment JSON + recommendation | Final report + edge + EV + sentiment |

Progress is printed as each step runs:
```
  [0/4] Web Search ...
  [0/4] Social Search ...
  [1/4] Running Research Agent ...
  [2/4] Running Critique Agent ...
  [3/4] Running Rebuttal Agent ...
  [4/4] Running Consolidation Agent ...
```

After Stage 4, the script automatically runs four post-analysis displays:

**Edge analysis** — compares the LLM's probability estimate against Polymarket's implied probability:

```
╔═════════════════════════════════════════════════════════════╗
║  EDGE ANALYSIS  (threshold: 5.0%)                           ║
╠══════════════════════╦════════════╦════════════╦════════════╣
║ Outcome              ║ Market %   ║ LLM Est %  ║ Edge       ║
╠══════════════════════╬════════════╬════════════╬════════════╣
║ Yes                  ║   35.00%   ║   55.00%   ║ +20.0% ▲  ║
║ No                   ║   65.00%   ║   45.00%   ║ -20.0% ▼  ║
╚══════════════════════╩════════════╩════════════╩════════════╝

  *** RECOMMENDED POSITION: BUY YES  (edge +20.0%) ***
```

If no outcome exceeds the edge threshold: `No edge detected above threshold (5.0%).`

**Expected value analysis** — computes EV and ROI per $1 contract based on LLM probability estimates:

```
╔══════════════════════════════════════════════════════════════════╗
║  EXPECTED VALUE  (per $1 contract)                               ║
╠══════════════════════╦════════════╦════════════╦════════════════╣
║ Outcome              ║ Buy at     ║ LLM Prob   ║ EV / ROI       ║
╠══════════════════════╬════════════╬════════════╬════════════════╣
║ Yes                  ║  $0.3500   ║   55.00%   ║ +0.20  +57.1% ▲║
║ No                   ║  $0.6500   ║   45.00%   ║ -0.20  -30.8% ▼║
╚══════════════════════╩════════════╩════════════╩════════════════╝

  Best EV: BUY YES at $0.35  →  +0.20 per contract  (+57.1% ROI)
```

**Social sentiment analysis** — extracted from the Consolidation agent's structured JSON output:

```
╔══════════════════════════════════════════════════╗
║  SOCIAL SENTIMENT  (X / web signals)             ║
╠══════════════════╦══════════════╦════════════════╣
║ Overall tone     ║ Bullish      ║ ▲▲  Bullish    ║
║ Score            ║ 0.68         ║                ║
║ Discussion vol   ║ High         ║                ║
╚══════════════════╩══════════════╩════════════════╝

  Signals : "strong X buzz"  |  "Reddit community bullish"
  Summary : Community broadly expects a Yes resolution based on recent news.
```

```bash
# Deep research with Kimi (Brave Search enrichment automatic)
uv run 09_odds_calculator.py btc-100k-2025 --llm kimi --deep-research

# Lower edge threshold to 3%
uv run 09_odds_calculator.py btc-100k-2025 --deep-research --edge-threshold 3

# Date search → deep research with Kimi
uv run 09_odds_calculator.py --date 2026-03-15 --llm kimi --deep-research

# Keyword search → deep research with OpenAI
uv run 09_odds_calculator.py --search "bitcoin" --pick 0 --llm openai --deep-research

# Single-pass + web search
uv run 09_odds_calculator.py btc-100k-2025 --web-search

# Deep research + raw JSON
uv run 09_odds_calculator.py btc-100k-2025 --deep-research --verbose
```

##### Liquidity sorting & filtering

When using `--date` or `--search`, the script fetches volume data for every candidate market in parallel (up to 10 concurrent requests) and then:

1. **Filters** out markets below the minimum notional traded threshold (default **$1 000 USD**).
2. **Sorts** remaining markets by notional traded (descending) before applying `--limit`.
3. **Shows** notional traded + open interest everywhere — market listing, odds header, per-market PDF info card, consolidated summary table, and consolidated PDF.

The volume line in the odds header looks like:
```
  Volume:    $48.2k vol · 12,430 OI
```

Use `--min-volume 0` to disable filtering, or raise it to focus on high-liquidity markets only. When analysing a market by slug directly, filtering is skipped (the requested market is always analysed regardless of volume).

```bash
# Multi-market run — automatically sorted by liquidity
uv run 09_odds_calculator.py --date 2026-02-25 --search "NBA" --limit 5

# Only show markets with at least $10 000 traded
uv run 09_odds_calculator.py --date 2026-02-25 --limit 10 --min-volume 10000

# Disable volume filter (include all markets regardless of liquidity)
uv run 09_odds_calculator.py --search "bitcoin" --limit 20 --min-volume 0
```

##### Multi-market analysis & consolidated report

When using `--search` or `--date` **without** `--pick`, the script shows a numbered list of all matched markets (sorted by liquidity), then runs the full analysis on every one in sequence. Use `--pick N` to analyse only result N.

After all markets are processed, a **consolidated summary** is printed to the terminal (when 2 or more markets were analysed):

```
════════════════════════════════════════════════════════════════════════════════════════════════════════
  CONSOLIDATED SUMMARY — 2026-02-19  (3 markets)
════════════════════════════════════════════════════════════════════════════════════════════════════════
  Market                          Best Edge    Best EV    Sentiment  Rec         Volume
  ────────────────────────────────────────────────────────────────────────────────────────────────────
  Lakers vs Celtics (...)         YES +8.2%▲   +0.06/c    Bullish    BUY YES     $48.2k vol · 12,430 OI
  Knicks vs Heat (...)            NO  -3.1%▼   -0.02/c    Neutral    —           $31.0k vol · 8,100 OI
  BTC 100k (...)                  YES +11.4%▲  +0.09/c    Bullish    BUY YES     $1.2M vol · 420,000 OI
  ────────────────────────────────────────────────────────────────────────────────────────────────────
  Top 2 picks by edge  :
    #1  BTC 100k (...)           edge +11.4%  $1.2M vol · 420,000 OI BUY YES
    #2  Lakers vs Celtics (...)  edge +8.2%   $48.2k vol · 12,430 OI BUY YES
  Top 2 picks by EV    :
    #1  BTC 100k (...)           EV +0.09/c   $1.2M vol · 420,000 OI BUY YES
    #2  Lakers vs Celtics (...)  EV +0.06/c   $48.2k vol · 12,430 OI BUY YES
════════════════════════════════════════════════════════════════════════════════════════════════════════
```

```bash
# Show all NBA markets resolving Feb 25 and analyse each one
uv run 09_odds_calculator.py --date 2026-02-25 --search "NBA" --limit 5

# Show the list but analyse only result #2
uv run 09_odds_calculator.py --date 2026-02-25 --search "NBA" --limit 5 --pick 2
```

##### PDF output (`--pdf`)

Saves polished, colour-coded PDF reports to a **dated subfolder** `reports/YYYY-MM-DD/`. Each per-market PDF contains every section printed to the terminal — odds table, LLM analysis, edge analysis, EV analysis, social sentiment, sources, and run summary — formatted with headers, bordered tables, and colour highlights. Volume (notional traded + open interest) appears in the market info card.

When a multi-market run produces 2 or more reports, an additional **consolidated PDF** is generated alongside the individual files. It contains:
- Title banner with date, market count, and pipeline info
- Summary table (one row per market): market name, best edge, best EV, sentiment, ROI, recommendation, volume
- Top picks by edge (ranked, with volume and recommendation)
- Top picks by EV (ranked, with ROI, volume, and recommendation)
- Markets to avoid (no edge and no positive EV)
- Mini odds overview (compact outcome/edge/EV table per market)

**Output folder structure:**
```
reports/
  2026-02-19/
    lakers-celtics_20260219_143022.pdf      ← individual per-market PDF
    knicks-heat_20260219_143105.pdf
    btc-100k-2025_20260219_143148.pdf
    consolidated_20260219_143200.pdf        ← consolidated PDF (2+ markets only)
```

The `reports/` folder is git-ignored and intended for local reading only.

```bash
# Auto-named PDF in reports/YYYY-MM-DD/
uv run 09_odds_calculator.py btc-100k-2025 --pdf

# Specific filename (basename placed in the dated subfolder)
uv run 09_odds_calculator.py btc-100k-2025 --pdf btc_report.pdf

# Deep research → per-market PDF + consolidated PDF (if 2+ markets)
uv run 09_odds_calculator.py --date 2026-02-25 --limit 3 --pick 0 --deep-research --pdf

# Odds only (no LLM) → save odds table as PDF
uv run 09_odds_calculator.py btc-100k-2025 --no-llm --pdf
```

> Requires the `reportlab` package (already listed in `pyproject.toml`). Run `uv sync` if not yet installed.

##### Run summary footer

Every run (including `--no-llm`) prints a summary at the end:

```
========================================================================
  RUN SUMMARY
========================================================================
  Provider  : kimi  (kimi-for-coding)
  Pipeline  : deep-research
  Agents    : Web Search → Social Search → Research → Critique → Rebuttal → Consolidation
  Duration  : 38.4 s

  SOURCES REFERENCED  (6 from web search)
  ------------------------------------------------------------------------
  [ 1] Golden State Warriors vs. Lakers: Game Preview & Stats
        https://www.nba.com/game/...
  [ 2] Lakers 2025-26 Season Statistics | Basketball-Reference
        https://www.basketball-reference.com/...
  ...
========================================================================
```

##### Odds formats computed

| Format | Example | Description |
|--------|---------|-------------|
| Implied probability | 62.50% | Direct from Polymarket price |
| Decimal (European) | 1.6000 | Return per $1 staked including stake |
| American (moneyline) | −167 / +150 | Negative = favourite, positive = underdog |
| Fractional (UK) | 3/5 | Profit relative to stake |
| Book overround | +3.20% | Combined vig/juice across all outcomes |

##### All flags

| Flag | Default | Description |
|------|---------|-------------|
| `slug` | — | Market slug to analyse (e.g. `btc-100k-2025`) |
| `--search QUERY` | — | Find a market by keyword |
| `--date YYYY-MM-DD` | — | Find markets resolving on this date (Eastern Time) |
| `--pick N` | *(all)* | Which listed result to analyse (0-indexed); omit to analyse all |
| `--limit N` | `10` | Max markets to show when using `--search` or `--date` |
| `--min-volume USD` | `1000` | Exclude markets with notional traded below this threshold (0 = no filter) |
| `--no-llm` | off | Show odds table only, skip all LLM calls |
| `--llm PROVIDER` | `claude` | LLM provider: `claude`, `openai`, `kimi`, `custom` |
| `--model MODEL` | *(per-provider default)* | Override the model ID |
| `--llm-base-url URL` | — | Base URL for a custom OpenAI-compatible endpoint |
| `--llm-api-key KEY` | — | Inline API key override (otherwise read from env) |
| `--deep-research` | off | Enable web + social search + 4-stage pipeline + edge + EV + sentiment |
| `--web-search` | off | Run Brave Search to enrich single-pass analysis |
| `--edge-threshold N` | `5` | Minimum % edge to flag a recommended position |
| `--verbose` | off | Print raw market JSON |
| `--pdf [FILENAME]` | — | Save PDF report(s) in `reports/YYYY-MM-DD/`; generates a consolidated PDF when 2+ markets are analysed |

> `slug`, `--search`, and `--date` are mutually exclusive. The `--no-llm` flag requires no API key at all. `BRAVE_SEARCH_API_KEY` enables web enrichment for `--web-search` and `--deep-research`; if unset the pipeline continues without web context and prints a warning. PDFs are git-ignored and placed in `reports/YYYY-MM-DD/` for local reading only.

**SDK methods:** `client.markets.retrieve_by_slug()`, `client.search.query()`

---

### Authenticated Endpoints (API Keys Required)

#### `04_account_portfolio.py` — Account & Portfolio

View account balances, open positions, and recent activity history.

```bash
uv run 04_account_portfolio.py                   # Full overview
uv run 04_account_portfolio.py --positions-only   # Only positions
uv run 04_account_portfolio.py --activities-only  # Only activity log
```

**SDK methods:** `client.account.balances()`, `client.portfolio.positions()`, `client.portfolio.activities()`

---

#### `05_place_order.py` — Preview & Place Orders

Preview orders (dry-run), place limit orders with confirmation, list open orders, or cancel all.

```bash
# Preview only (does NOT submit)
uv run 05_place_order.py --market btc-100k-2025 --side long --price 0.55 --qty 10 --preview-only

# Place a limit order (prompts for confirmation)
uv run 05_place_order.py --market btc-100k-2025 --side long --price 0.55 --qty 10

# Short side with fill-or-kill
uv run 05_place_order.py --market btc-100k-2025 --side short --price 0.45 --qty 5 --tif fok

# List open orders
uv run 05_place_order.py --list-open

# Cancel all open orders
uv run 05_place_order.py --cancel-all
```

**SDK methods:** `client.orders.preview()`, `client.orders.create()`, `client.orders.list()`, `client.orders.cancel_all()`

**Order parameters:**

| Flag | Values | Description |
|------|--------|-------------|
| `--side` | `long`, `short` | Maps to `ORDER_INTENT_BUY_LONG` / `ORDER_INTENT_BUY_SHORT` |
| `--price` | `0.01` – `0.99` | Limit price in USD |
| `--qty` | integer | Number of whole contracts (fractional not supported) |
| `--tif` | `gtc`, `ioc`, `fok` | Good-til-cancel (default), immediate-or-cancel, fill-or-kill |

---

#### `06_async_dashboard.py` — Async Dashboard

Fetch multiple API resources concurrently using `AsyncPolymarketUS` and `asyncio.gather`.

```bash
uv run 06_async_dashboard.py                    # Balances + positions + activity
uv run 06_async_dashboard.py --include-markets   # Also fetch trending events & markets
```

**SDK methods:** `AsyncPolymarketUS` context manager, `asyncio.gather()` for concurrent requests

---

#### `07_websocket_stream.py` — Real-Time WebSocket Streaming

Stream live market data (order book, trades) and/or private updates (orders, positions, balances) via WebSocket.

```bash
# Stream market data for one market
uv run 07_websocket_stream.py --market btc-100k-2025

# Stream multiple markets
uv run 07_websocket_stream.py --market btc-100k-2025 --market eth-5k-2025

# Stream private channel only (orders, positions, balances)
uv run 07_websocket_stream.py --private

# Stream both market data and private channel
uv run 07_websocket_stream.py --market btc-100k-2025 --private
```

Press `Ctrl+C` to stop streaming.

**SDK methods:** `client.ws.markets()`, `client.ws.private()`, `.on()` event callbacks, `.subscribe()`

**WebSocket events:**

| Channel | Events |
|---------|--------|
| Markets | `market_data`, `market_data_lite`, `trade`, `heartbeat` |
| Private | `order_snapshot`, `order_update`, `position_snapshot`, `position_update`, `account_balance_snapshot`, `account_balance_update`, `heartbeat` |

## SDK Reference

All scripts use the [`polymarket-us`](https://pypi.org/project/polymarket-us/) Python SDK (v0.1.2+).

### Client Initialization

```python
# Public (no auth)
from polymarket_us import PolymarketUS
client = PolymarketUS()

# Authenticated
client = PolymarketUS(key_id="...", secret_key="...")

# Async
from polymarket_us import AsyncPolymarketUS
async with AsyncPolymarketUS(key_id="...", secret_key="...") as client:
    ...
```

### Available Resources

| Resource | Methods |
|----------|---------|
| `client.events` | `list()`, `retrieve(id)`, `retrieve_by_slug(slug)` |
| `client.markets` | `list()`, `retrieve(id)`, `retrieve_by_slug(slug)`, `book(slug)`, `bbo(slug)`, `settlement(slug)` |
| `client.search` | `query(params)` |
| `client.series` | `list()`, `retrieve(id)` |
| `client.sports` | `list()`, `teams()` |
| `client.orders` | `create()`, `preview()`, `list()`, `retrieve(id)`, `modify()`, `cancel()`, `cancel_all()`, `close_position()` |
| `client.portfolio` | `positions()`, `activities()` |
| `client.account` | `balances()` |
| `client.ws` | `markets()`, `private()` *(async only)* |

### Data Model Notes

**Event fields:** `title`, `slug`, `closed`, `active`, `category`, `startDate`, `markets[]`

**Market fields:** `question` (not `title`), `slug`, `closed`, `active`, `marketType`, `outcomes` (JSON string), `outcomePrices` (JSON string)

> Markets use `question` for the display text. The `outcomes` and `outcomePrices` fields are JSON-encoded strings (e.g., `'["Yes","No"]'`). Parse them with `json.loads()` before use.

**BBO / Book responses:** Data is nested under a `marketData` key. Access fields like `bestBid`, `bestAsk`, `bids`, `offers`, and `stats` via `response["marketData"]`.

**WebSocket callbacks:** The `.on()` handler receives the full top-level dict. Actual data is nested under keys specific to each event type:

| Event | Data key |
|-------|----------|
| `market_data` | `data["marketData"]` |
| `market_data_lite` | `data["marketDataLite"]` |
| `trade` | `data["trade"]` |
| `order_snapshot` | `data["orderSubscriptionSnapshot"]` |
| `order_update` | `data["orderSubscriptionUpdate"]` |
| `position_snapshot` | `data["positionSubscriptionSnapshot"]` |
| `position_update` | `data["positionSubscriptionUpdate"]` |
| `account_balance_snapshot` | `data["accountBalanceSubscriptionSnapshot"]` |
| `account_balance_update` | `data["accountBalanceSubscriptionUpdate"]` |

### Error Handling

```python
from polymarket_us import (
    AuthenticationError,   # Invalid/missing credentials
    BadRequestError,       # Malformed request
    NotFoundError,         # Resource not found
    RateLimitError,        # Rate limit exceeded
    APITimeoutError,       # Request timed out
    APIConnectionError,    # Network failure
)
```

## Project Structure

```
polymarket-us-init/
├── .env.example              # API credentials template (all providers)
├── .python-version           # Python 3.13
├── pyproject.toml            # Project config & dependencies
├── uv.lock                   # Locked dependency versions
├── reports/                  # PDF output folder (git-ignored)
│   └── YYYY-MM-DD/           #   dated subfolder per run
│       ├── <slug>_<ts>.pdf   #   per-market PDF
│       └── consolidated_<ts>.pdf  # consolidated PDF (2+ markets)
├── 01_browse_markets.py      # Public  — list events & markets
├── 02_search_markets.py      # Public  — full-text search
├── 03_orderbook_viewer.py    # Public  — order book & BBO
├── 04_account_portfolio.py   # Auth    — balances, positions, activity
├── 05_place_order.py         # Auth    — preview & place orders
├── 06_async_dashboard.py     # Auth    — async concurrent dashboard
├── 07_websocket_stream.py    # Auth    — real-time WebSocket streaming
├── 08_markets_by_date.py     # Public  — filter markets by resolution date + keyword
└── 09_odds_calculator.py     # Public  — odds calculator + multi-provider LLM analysis
                              #           with liquidity sorting, consolidated report,
                              #           and optional 4-stage deep research pipeline
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `polymarket-us` | Polymarket US API SDK |
| `anthropic` | Claude / Kimi LLM client (script 09, `--llm claude` or `--llm kimi`) |
| `openai` | OpenAI / custom-endpoint client (script 09, `--llm openai/custom`) |
| `httpx` | HTTP client for Brave Search API calls (script 09 web search) |
| `python-dotenv` | Load `.env` credentials automatically |
| `reportlab` | PDF generation for `--pdf` output (script 09) |

## Links

- [Polymarket US API Docs](https://docs.polymarket.us/api/introduction)
- [Polymarket US SDK Docs](https://docs.polymarket.us/sdks/introduction)
- [Python SDK on PyPI](https://pypi.org/project/polymarket-us/)
- [Python SDK on GitHub](https://github.com/Polymarket/polymarket-us-python)
- [Developer Portal](https://polymarket.us/developer) (API key generation)

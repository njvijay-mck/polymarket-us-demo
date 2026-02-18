# Polymarket US API Demo Scripts

Python scripts demonstrating the [Polymarket US](https://polymarket.us) retail API and SDK. Covers public market data, date-based market filtering, odds calculation, LLM-powered probability analysis, authenticated trading, async concurrency, and real-time WebSocket streaming.

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- **Polymarket US account** (for authenticated endpoints) — download the iOS app, create an account, and complete identity verification
- **API keys** (for authenticated endpoints) — generate at [polymarket.us/developer](https://polymarket.us/developer)

## Setup

```bash
# Clone and enter the project
cd polymarket-us-init

# Install dependencies (creates .venv automatically)
uv sync
```

### API Key Configuration

Scripts 04-07 require Polymarket authentication. Script 09 requires an Anthropic API key for the LLM analysis step. All scripts use `python-dotenv` to auto-load credentials from a `.env` file.

**Recommended: `.env` file**

```bash
cp .env.example .env
# Edit .env with your actual keys
```

The `.env` file contains:

```
POLYMARKET_KEY_ID=your-key-id-uuid
POLYMARKET_SECRET_KEY=your-base64-encoded-ed25519-private-key
ANTHROPIC_API_KEY=your-anthropic-api-key
```

**Fallback: environment variables**

If you prefer not to use a `.env` file, you can set the variables directly in your shell:

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

Fetches live market prices, converts them into every major odds format, and passes the data to Claude for plain-English win-probability analysis.

```bash
# Full analysis for a market slug
uv run 09_odds_calculator.py btc-100k-2025

# Odds table only — skip the LLM step
uv run 09_odds_calculator.py btc-100k-2025 --no-llm

# Search for a market then analyse the first result
uv run 09_odds_calculator.py --search "bitcoin" --pick 0

# Use a more powerful model for deeper analysis
uv run 09_odds_calculator.py btc-100k-2025 --model claude-opus-4-5

# Show raw market JSON alongside the odds
uv run 09_odds_calculator.py btc-100k-2025 --verbose
```

**SDK methods:** `client.markets.retrieve_by_slug()`, `client.search.query()`

**LLM:** Uses the [Anthropic Python SDK](https://github.com/anthropic/anthropic-sdk-python) (`anthropic` package). Requires `ANTHROPIC_API_KEY` in `.env`.

**Odds formats computed:**

| Format | Example | Description |
|--------|---------|-------------|
| Implied probability | 62.50% | Direct from Polymarket price |
| Decimal (European) | 1.6000 | Return per $1 staked including stake |
| American (moneyline) | -167 / +150 | Negative = favourite, positive = underdog |
| Fractional (UK) | 3/5 | Profit relative to stake |
| Book overround | +3.20% | Combined vig/juice across all outcomes |

**Arguments:**

| Flag | Description |
|------|-------------|
| `slug` | Market slug to analyse (e.g. `btc-100k-2025`) |
| `--search QUERY` | Find a market by keyword instead of slug |
| `--pick N` | Which search result to analyse (0-indexed, default: 0) |
| `--no-llm` | Skip Claude analysis, show odds table only |
| `--model MODEL` | Anthropic model (default: `claude-haiku-4-5`) |
| `--verbose` | Print raw market JSON |

> `slug` and `--search` are mutually exclusive. The LLM step is free to skip with `--no-llm` if you don't have an Anthropic API key.

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
| `--price` | `0.01` - `0.99` | Limit price in USD |
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
├── .env.example              # API credentials template
├── .python-version           # Python 3.13
├── pyproject.toml            # Project config & dependencies
├── uv.lock                   # Locked dependency versions
├── 01_browse_markets.py      # Public  — list events & markets
├── 02_search_markets.py      # Public  — full-text search
├── 03_orderbook_viewer.py    # Public  — order book & BBO
├── 04_account_portfolio.py   # Auth    — balances, positions, activity
├── 05_place_order.py         # Auth    — preview & place orders
├── 06_async_dashboard.py     # Auth    — async concurrent dashboard
├── 07_websocket_stream.py    # Auth    — real-time WebSocket streaming
├── 08_markets_by_date.py     # Public  — filter markets by resolution date + keyword
└── 09_odds_calculator.py     # Public  — odds calculator + Claude LLM analysis
```

## Links

- [Polymarket US API Docs](https://docs.polymarket.us/api/introduction)
- [Polymarket US SDK Docs](https://docs.polymarket.us/sdks/introduction)
- [Python SDK on PyPI](https://pypi.org/project/polymarket-us/)
- [Python SDK on GitHub](https://github.com/Polymarket/polymarket-us-python)
- [Developer Portal](https://polymarket.us/developer) (API key generation)

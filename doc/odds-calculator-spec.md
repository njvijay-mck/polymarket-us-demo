# Odds Calculator — Platform-Agnostic Specification

A CLI tool that fetches prediction market data from a public API, converts prices into standard betting odds formats, optionally enriches the analysis with web search, and runs one or more LLM agents to estimate true probabilities and recommend trading positions. PDF reports can be saved for later review.

This spec describes logic and behaviour only. All references to specific platform SDKs, API schemas, or authentication details are intentionally omitted so the tool can be re-implemented against any prediction market API.

---

## 1. Data Model

### 1.1 Core Dataclasses

#### `RunMetrics`
Populated throughout a single script execution and displayed at the end.

| Field | Type | Description |
|---|---|---|
| `provider` | `str` | LLM provider name (e.g. "claude", "openai") |
| `model` | `str` | Specific model ID used |
| `pipeline` | `str` | `"single-pass"`, `"deep-research"`, or `"none (--no-llm)"` |
| `agents_run` | `list[str]` | Ordered list of agents/stages executed (e.g. `["Web Search", "Research", "Critique", ...]`) |
| `search_queries` | `list[str]` | All web search queries issued |
| `sources` | `list[{title, url}]` | Web sources returned by search results |
| `start_time` | `float` | `time.monotonic()` at construction — used to compute `elapsed_s()` |

#### `ReportData`
Accumulates all output for a single market. Used by both single-market and consolidated PDF generation.

| Field | Type | Description |
|---|---|---|
| `market` | `dict` | Raw market dict from the API |
| `rows` | `list[dict]` | Output of `build_odds_table()` |
| `analysis_text` | `str` | Full LLM output (single-pass analysis or deep research final stage) |
| `llm_probs` | `list[dict]` | Parsed probability array from LLM output |
| `sentiment` | `dict \| None` | Parsed sentiment object from consolidation stage |
| `metrics` | `RunMetrics \| None` | Run metrics for this market |
| `is_deep_research` | `bool` | Whether the deep research pipeline was used |
| `edge_threshold` | `float` | Minimum % edge to flag a recommended position (default: 5.0) |
| `summary` | `dict \| None` | One-row summary dict computed by `_compute_market_summary()` |

#### `ConsolidatedReport`
Holds all `ReportData` objects from a multi-market run.

| Field | Type | Description |
|---|---|---|
| `reports` | `list[ReportData]` | One entry per market analysed |
| `edge_threshold` | `float` | Edge threshold used across the run |
| `run_date` | `str` | `YYYY-MM-DD` in the local timezone (Eastern by default) |
| `top_n` | `int` | Max picks shown in top-by-edge and top-by-EV ranked lists |

---

## 2. CLI Interface

### 2.1 Market Source (mutually exclusive, one required)

| Argument | Description |
|---|---|
| `slug` (positional) | Direct market identifier — fetch exactly one market |
| `--search QUERY` | Keyword search across all markets |
| `--date YYYY-MM-DD` | Find markets whose event falls on this date (timezone: local/Eastern) |

### 2.2 Selection & Filtering

| Flag | Default | Description |
|---|---|---|
| `--pick N` | all | When using `--search` or `--date`, analyse only the N-th result (0-indexed) after volume sorting |
| `--limit N` | 10 | Max markets to display and analyse after filtering |
| `--min-volume USD` | 1000 | Exclude markets below this notional traded threshold |

### 2.3 LLM Provider

| Flag | Default | Description |
|---|---|---|
| `--no-llm` | off | Skip all LLM calls; display odds table only |
| `--llm PROVIDER` | `claude` | Provider: `claude`, `openai`, `kimi`, `custom` |
| `--model MODEL` | provider default | Override the default model for the chosen provider |
| `--llm-api-key KEY` | from env | API key override (otherwise read from environment) |
| `--llm-base-url URL` | — | Required for `--llm custom`; also used internally for Kimi |

### 2.4 Analysis Pipeline

| Flag | Default | Description |
|---|---|---|
| `--deep-research` | off | Run the 4-stage pipeline: Web Search → Research → Critique → Rebuttal → Consolidation |
| `--web-search` | off | Run a single web search and pass results to a single-pass LLM call |
| `--edge-threshold N` | 5.0 | Minimum % edge to print a "RECOMMENDED POSITION" banner |

### 2.5 Output

| Flag | Default | Description |
|---|---|---|
| `--pdf [FILENAME]` | — | Save a PDF report. If no filename is given, auto-name as `<slug>_<timestamp>.pdf`. Files are placed in `reports/YYYY-MM-DD/`. For multi-market runs a consolidated PDF is also saved. |
| `--verbose` | off | Print the raw market JSON after the odds table |

---

## 3. Market Fetching

### 3.1 Single Slug

Call the API's market-by-identifier endpoint. Unwrap any envelope (e.g. `{"market": {...}}`) and return the inner dict. Exit with an error message if not found.

Attach volume data (see §3.4) without applying any minimum-volume filter.

### 3.2 Keyword Search

1. Fetch up to `max(limit × 5, 50)` results from the search endpoint.
2. Flatten all markets out of any nested event/market structure.
3. Enrich with volume data (§3.4), apply `min_volume` filter, sort by volume descending.
4. Truncate to `--limit`.
5. Print a numbered list: `[N] <question>  [<category>]  <volume string>`.
6. If `--pick` is set, return only that one market; otherwise return all.

### 3.3 Date Search

The goal is to find all markets whose **event** falls on the target date.

**Pagination strategy:**
- Page through the full market list in pages of 100, up to 70 pages (7,000 records total).
- Do **not** break on a short page (< 100 records) — short pages can appear mid-dataset due to API quirks. Break only on an empty page.
- Apply an early-exit optimisation: once every market in a page has a settlement date strictly after the target date, no further matches exist ahead (markets are sorted by settlement date ascending).

**Date matching (two cases):**
- **Sports/event markets** — if the market identifier (slug) ends with a date string `YYYY-MM-DD`, use that as the event date and match against it regardless of the settlement deadline.
- **Non-event markets** — no date in the identifier; fall back to the settlement `endDate` converted to the local timezone (Eastern).

The reason for this split: sports markets are often settled 1–14 days after the event, so their `endDate` does not represent the game date.

After collecting all candidates:
1. Enrich with volume data (§3.4), apply `min_volume` filter, sort by volume descending.
2. Truncate to `--limit`.
3. Print a numbered list showing both the event date (if different from settlement) and the settlement deadline.
4. Apply `--pick` selection if set.

### 3.4 Volume Enrichment

Fetches liquidity metrics for a list of markets concurrently (up to 10 parallel requests).

For each market, fetch from the platform's order-book or stats endpoint and extract:
- `notional_usd` — total notional value traded
- `open_interest` — current open interest

Attach these as private keys (`_notional_usd`, `_open_interest`) on the market dict.

Apply the `min_volume_usd` filter (drop markets below threshold), then sort descending by `(notional_usd, open_interest)`.

Print a progress message: `"Fetching volume for N market(s) ..."` followed by the count excluded below threshold.

**Volume display format:**
- ≥ $1M → `$X.XM`
- ≥ $1k → `$X.Xk`
- Otherwise → `$X`
- Appended with open interest: `$12.3k vol · 5,412 OI`

### 3.5 Started-Game Filtering

For `--search` and `--date` modes (not direct slug), after fetching markets:
- Check each market's `gameStartTime` field (ISO UTC string).
- If `gameStartTime` is in the past relative to current UTC time, skip that market.
- Markets without `gameStartTime` are always kept (fail-open).
- Print a summary of skipped markets before proceeding.

---

## 4. Odds Table

### 4.1 Data Shape Handling

Two API shapes must be supported:

**Shape A — Standard markets:**
`outcomes` and `outcomePrices` are JSON-encoded strings that decode to parallel arrays (e.g. `["Yes", "No"]` and `["0.62", "0.38"]`). Parse both, zip them, and convert each price string to a float.

**Shape B — Sports moneyline markets:**
A `marketSides` list where each element is a dict with a `price` field and a nested `team` object containing `name` and optionally `record`. Construct the outcome label as `"Team Name (W-L-T)"` when a record is present.

If neither shape yields data, return an empty list.

### 4.2 Odds Calculations

Given a price `p` (the market's implied probability, range 0–1):

| Format | Formula |
|---|---|
| **Implied probability** | `clamp(p, 0, 1)` |
| **Decimal odds** | `1 / p` (rounded to 4 decimal places) |
| **American moneyline** | If `p ≥ 0.5`: `round(-(p / (1-p)) × 100)` formatted as `"-NNN"`. If `p < 0.5`: `round(((1-p) / p) × 100)` formatted as `"+NNN"`. |
| **Fractional odds** | `(1-p) : p` reduced to lowest terms via GCD of rounded integer numerator and denominator. |
| **Book overround (vig)** | `(sum of all implied probabilities - 1.0) × 100`, expressed as a signed percentage. Positive = bookmaker favoured; negative = bettor favoured. |

Each outcome row in the result:
```
{
  "outcome":      str,    # label
  "price":        float,  # raw market price
  "implied_prob": float,  # clamped probability
  "prob_pct":     float,  # percentage rounded to 2 dp
  "decimal":      float,
  "american":     str,    # e.g. "+162" or "-163"
  "fractional":   str,    # e.g. "13/8" or "N/A"
}
```

### 4.3 Terminal Display

Print a fixed-width table with columns: Outcome, Price, Implied %, Decimal, American, Fractional. Follow with the book overround. Show market metadata (identifier, status, category, settlement time, event date if different, volume) in a header block above the table.

Print raw market JSON when `--verbose` is set.

---

## 5. Outcome Display Labels

When displaying outcomes in edge/EV tables, apply smart annotation to disambiguate sports team labels.

**Rule set (evaluated in priority order):**

1. **YES/NO binary markets** — return labels unchanged.
2. Extract team names from the question string by splitting on `" vs. "`, `" vs "`, `" or "`, or `" / "`. Strip common question prefixes (`"Will "`, `"Who wins: "`, etc.) from the first team.
3. For each outcome label:
   - **Exact match** — the label matches a team name exactly → keep as-is.
   - **Suffix match** — the label is a trailing word of a full team name (e.g. `"Lakers"` inside `"Los Angeles Lakers"`) → annotate as `"Lakers (Los Angeles)"`.
   - **No overlap** — the label shares no text with any team name (pure mascot) → use positional mapping: outcome at index `i` gets the `i`-th team name as annotation → `"Wildcats (New Hampshire)"`.
4. If no team names could be extracted from the question, return labels unchanged.

---

## 6. LLM Client Abstraction

### 6.1 Interface

Both clients expose a single method:
```
.chat(system: str, user: str, max_tokens: int = 2048) -> str
```

### 6.2 `AnthropicLLMClient`

Wraps the Anthropic Python SDK. Accepts an optional `base_url` for compatible third-party endpoints (e.g. providers that implement the Anthropic messages API). Passes `system` as a top-level `system` parameter and `user` as a single user message.

### 6.3 `OpenAICompatibleLLMClient`

Wraps the OpenAI Python SDK. Accepts an optional `base_url` for custom endpoints (Groq, DeepSeek, Gemini OpenAI-compat mode, Ollama, etc.). Sends `system` as a `role: system` message and `user` as a `role: user` message.

### 6.4 Provider Routing

| Provider key | SDK used | API key env var | Base URL |
|---|---|---|---|
| `claude` | Anthropic | `ANTHROPIC_API_KEY` | default (Anthropic) |
| `openai` | OpenAI | `OPENAI_API_KEY` | default (OpenAI) |
| `kimi` | Anthropic (Kimi speaks Anthropic API) | `KIMI_API_KEY` | Kimi coding endpoint |
| `custom` | OpenAI-compatible | `LLM_API_KEY` | `--llm-base-url` (required) |

Default models (overridable with `--model`):
- `claude` → `claude-haiku-4-5` (or equivalent fast model)
- `openai` → `gpt-4o`
- `kimi` → provider-specific coding model

Exit with a descriptive error if a required API key or base URL is missing.

---

## 7. Market Payload Builder

Before sending to the LLM, construct a structured payload dict:

```json
{
  "question":       "...",
  "description":    "...",
  "category":       "...",
  "status":         "Active|Closed|Archived|Inactive",
  "settles_utc":    "YYYY-MM-DD HH:MM:SS",
  "settles_et":     "YYYY-MM-DD HH:MM ET",
  "event_date":     "YYYY-MM-DD",   // only if event date differs from settlement date
  "outcomes": [
    {
      "outcome":       "...",
      "market_price":  0.62,
      "implied_prob":  "62.00%",
      "decimal_odds":  1.6129,
      "american_odds": "-163",
      "fractional":    "13/21"
    }
  ],
  "book_overround": "+0.00%"
}
```

---

## 8. Analysis Pipelines

### 8.1 Single-Pass Analysis

**System prompt contract:**
```
You are an expert prediction-market analyst. You receive structured market data.
1. Summarise the market in one sentence.
2. Identify the favourite and underdog(s) with win probability.
3. Assess whether market pricing is reasonable; flag mispricing or skew.
4. Calculate the implied edge for a contrarian position.
5. Give a concise recommendation: "value on [outcome]", "fairly priced", or "avoid".

MUST include a JSON probability block:
```json
[
  {"outcome": "Yes", "llm_probability": 0.62, "confidence": "medium", "confidence_reason": "stats"},
  ...
]
```
Probabilities must sum to 1.0.
confidence: "low" | "medium" | "high"
confidence_reason: exactly ONE word from the allowed legend (see §8.4)
```

**User message:** Serialise the market payload JSON and append any web search context.

**Web search (optional — `--web-search`):** Call `web_search_context()` before the LLM call and prepend the results to the user message.

### 8.2 Deep Research Pipeline (4 stages + 2 search steps)

**Step 0a — General Web Search:**
Query: `"<market question> statistics analysis <current year>"`
Fetch up to 6 results. Format as a markdown block with title, snippet (≤ 300 chars), and source URL.

**Step 0b — Social/Community Search:**
Two queries:
- `"<market question> twitter X sentiment public opinion <year>"`
- `"<market question> reddit community prediction discussion <year>"`
Fetch up to 5 results each. Format as a markdown block.

Both searches use the Brave Search API (`BRAVE_SEARCH_API_KEY`). If the key is absent or the request fails, log a warning and continue without web context (non-fatal).

**Stage 1 — Research Agent:**
System prompt sections: Key Facts · Base Rates · Recent Context · Risk Factors · Initial Probability Estimate.
Input: market payload JSON + web search results + social search results.

**Stage 2 — Critique Agent:**
System prompt sections: Gaps and Flaws · Cognitive Biases · Counter-Arguments · Alternative Scenarios · Key Open Questions.
Input: market payload + web context + Stage 1 research report.

**Stage 3 — Rebuttal Agent:**
System prompt sections: Point-by-Point Response · Updated Evidence · Revised Probability Estimate.
Input: market payload + web context + Stage 1 report + Stage 2 critique.

**Stage 4 — Consolidation Agent:**
System prompt sections: Summary · Final Probability Estimates · Confidence · Recommendation.

**Must include two structured blocks:**

JSON probability block (same schema as single-pass):
```json
[{"outcome": "...", "llm_probability": 0.62, "confidence": "medium", "confidence_reason": "stats"}, ...]
```

Sentiment JSON block:
```sentiment-json
{"overall": "bullish", "score": 0.72, "volume": "high",
 "signals": ["strong X buzz", "Reddit community bullish"],
 "summary": "Community strongly expects Yes resolution"}
```
- `overall`: `"bullish"` | `"bearish"` | `"neutral"`
- `score`: 0.0 (strongly bearish) → 1.0 (strongly bullish), 0.5 = neutral
- `volume`: `"low"` | `"moderate"` | `"high"` (social discussion activity)
- `signals`: 2–3 short phrases drawn from social/community evidence
- `summary`: one sentence
- When no social data is available, default to `neutral`, score `0.5`, volume `"low"`.

Input: market payload + web context + all three prior stage outputs.

Progress is printed before each stage (e.g. `"[1/4] Running Research Agent ..."`).

### 8.3 Output Parsing

**Probability array:** Search the LLM output for a fenced JSON block (` ```json `) containing a list where every element has `"outcome"` and `"llm_probability"` keys. Return the first valid match.

**Sentiment object:** Search the LLM output for a ` ```sentiment-json ` fenced block containing an object with `"overall"` and `"score"` keys. Return the first valid match. Return `None` if absent.

### 8.4 Confidence Reason Legend

One-word codes used in the probability JSON block (`confidence_reason`):

| Code | Meaning |
|---|---|
| `stats` | Team/player statistics drove the estimate |
| `injury` | Player injury status is the key variable |
| `form` | Recent form/performance trend is decisive |
| `news` | Breaking news materially changes the outlook |
| `data` | Market or historical base-rate data used |
| `record` | Head-to-head record is the main reference |
| `volume` | Trading volume signals informed positioning |
| `consensus` | Expert or public consensus is the anchor |
| `weather` | Weather/field conditions are the swing factor |
| `schedule` | Schedule strength or matchup context |
| `momentum` | Recent momentum swing is the key signal |
| `unclear` | Insufficient information to be confident |

---

## 9. Edge & EV Analysis

### 9.1 Edge Analysis

For each outcome, compute:
```
edge (%) = (llm_probability - market_implied_probability) × 100
```

Match LLM probability entries to market outcome rows by lowercased label (exact match first, then substring match as fallback).

Display as a box-drawing Unicode table with columns: Outcome | Market % | LLM Est % | Edge.

If the outcome with the largest absolute edge exceeds `--edge-threshold`:
```
*** RECOMMENDED POSITION: BUY <OUTCOME>  (edge +N.N%) ***
```
Direction: `BUY` when edge is positive (LLM thinks outcome is underpriced), `SELL` when negative.

### 9.2 EV Analysis

For binary prediction markets where a winning contract pays $1:
```
EV per $1 contract = llm_probability - market_price
ROI (%) = EV / market_price × 100
```

Display as a box-drawing Unicode table with columns: Outcome | Buy at | LLM Prob | EV / ROI.

Highlight the best (highest EV) outcome and print a summary line:
```
Best EV: BUY <OUTCOME> at $X.XX  →  +X.XX per contract  (+X.X% ROI)
```

### 9.3 Market Summary (`_compute_market_summary`)

Derives a flat dict from `ReportData` for use in consolidated display and PDF. No LLM calls.

Computed fields:

| Key | Derivation |
|---|---|
| `question` | Market question, truncated to 60 chars |
| `slug` | Market identifier |
| `best_edge_label` | Outcome with highest absolute edge |
| `best_edge` | Signed edge value (%) |
| `best_ev_label` | Outcome with highest positive EV |
| `best_ev` | EV per contract |
| `best_ev_price` | Market price of best-EV outcome |
| `roi` | ROI (%) for best-EV outcome |
| `sentiment` | `overall` from parsed sentiment (`None` if absent) |
| `sentiment_score` | Numeric score from parsed sentiment |
| `recommendation` | `"BUY <OUTCOME>"`, `"SELL <OUTCOME>"`, or `"—"` (edge-based) |
| `ev_recommendation` | `"BUY <OUTCOME>"` or `"—"` (EV-based) |
| `notional_usd` | Volume from enrichment |
| `open_interest` | OI from enrichment |
| `confidence` | First confidence level from `llm_probs` |
| `confidence_reason` | First confidence_reason from `llm_probs` (single word, max 12 chars) |
| `game_start` | Event start time formatted as `"Mon DD H:MMam/pm ET"` |

---

## 10. Social Sentiment Display

Rendered as a Unicode box table with rows: Overall tone | Score | Discussion volume | Signals | Summary.

Score → indicator mapping:

| Score range | Indicator |
|---|---|
| ≥ 0.70 | ▲▲▲  Strongly Bullish |
| 0.55 – 0.69 | ▲▲   Bullish |
| 0.45 – 0.54 | ───  Neutral |
| 0.30 – 0.44 | ▼▼   Bearish |
| < 0.30 | ▼▼▼  Strongly Bearish |

---

## 11. Run Metrics Display

Shown after each market's analysis:
- Provider and model
- Pipeline label
- Agent chain (e.g. `Web Search → Research → Critique → Rebuttal → Consolidation`)
- Elapsed time in seconds
- Sources: numbered list of `title` + `url` for each web result cited

---

## 12. Consolidated Terminal Display

Shown after all markets are analysed (when more than one market was processed).

**Summary table** (one row per market): Market | Best Edge | Best EV | Sentiment | Rec | Conf | Why | Volume

**Top picks by Edge** (up to `--limit`): ranked by absolute edge descending, showing Rank | Question | Edge | Volume | Recommendation | Confidence | Why

**Top picks by EV** (up to `--limit`): ranked by EV descending, showing Rank | Question | EV/c | ROI | Rec | Volume | Confidence | Why

**WHY column legend**: the confidence_reason codes and their meanings (§8.4) printed in a 2-column layout.

---

## 13. PDF Report Generation

PDFs are saved to `reports/YYYY-MM-DD/` (created automatically). The date is the run date in the local timezone (Eastern). Library: `reportlab`.

### 13.1 Single-Market PDF (`generate_pdf`)

Eight sections rendered as `reportlab` Platypus flowables:

| Section | Content |
|---|---|
| 1. Title Banner | Report title, generation timestamp, market identifier |
| 2. Market Information | Question, identifier, category, status, event date (if different from settlement), settlement datetime (full local timezone string), liquidity, description |
| 3. Odds Table | One row per outcome: Outcome, Price, Implied %, Decimal, American, Fractional. Book overround note below. |
| 4. LLM Analysis | Full analysis text rendered from LLM markdown (## headings, bullet lists, inline bold). Section header labels pipeline as "LLM ANALYSIS" or "DEEP RESEARCH ANALYSIS". |
| 5. Edge Analysis *(deep research only)* | Table: Outcome, Market %, LLM Est %, Edge. Edge cells coloured green (positive) or red (negative). Recommended position box if threshold exceeded. |
| 6. EV Analysis *(deep research only)* | Table: Outcome, Buy at, LLM Prob, EV/ROI. EV/ROI cells coloured green/red. Best EV summary line. |
| 7. Social Sentiment *(if available)* | Table: tone, score, discussion volume, signals, summary. Indicator text coloured to match sentiment. |
| 8. Run Summary | Provider, model, pipeline, agents, duration, sources list. |

Footer: tool name and disclaimer bar.

### 13.2 Consolidated Multi-Market PDF (`generate_consolidated_pdf`)

Seven sections:

| Section | Content |
|---|---|
| 1. Title Banner | "CONSOLIDATED MARKET REPORT — YYYY-MM-DD", market count, provider/pipeline |
| 2. Summary Table | One row per market: Market, Best Edge, Best EV, Sentiment, ROI, Rec, Conf, Why, Volume. Edge and EV columns coloured green/red. |
| 3. Top Picks by Edge | Ranked table: Rank, Market, Game Time, Edge, Volume, Rec, Conf, Why. Edge column coloured green/red. |
| 4. Top Picks by EV | Ranked table: Rank, Market, Game Time, EV/c, ROI, Rec, Volume, Conf, Why. |
| 5. Markets to Avoid | Markets with no edge above threshold AND no positive EV. Table: Market question, Edge and EV summary. Red colour scheme. |
| 6. Mini Odds per Market | For each market: a sub-table with columns Outcome, Market %, LLM %, Edge, EV/c, ROI. Shows all outcomes with LLM estimates if available. |
| 7. WHY Column Legend | Two-column table mapping confidence_reason codes to descriptions (§8.4). |

Footer: same as single-market PDF.

### 13.3 LLM Markdown Rendering

The analysis text is rendered line-by-line:
- `## Heading` → styled heading paragraph
- `### Heading` → bold body paragraph
- `- item` or `* item` → bullet paragraph with `•` prefix; inline `**bold**` converted to `<b>` tags
- Blank line → small spacer
- Other lines → body paragraph with inline bold conversion

### 13.4 File Naming

- **Auto-named single market:** `<identifier>_<YYYYMMDD_HHMMSS>.pdf`
- **Named single market:** `<basename of provided name>`
- **Consolidated:** `consolidated_<YYYYMMDD_HHMMSS>.pdf`
- All paths placed under `reports/YYYY-MM-DD/`.

---

## 14. Main Execution Flow

```
main()
│
├─ Parse CLI arguments
│
├─ Fetch markets
│   ├─ slug → fetch_market_by_slug() + volume enrichment (no filter)
│   ├─ --search → search_and_pick()
│   └─ --date → search_by_date()
│
├─ Filter started games (search/date only)
│   └─ Exit gracefully if all games have already started
│
├─ Create LLM client (once, reused across all markets)
│   └─ Skip if --no-llm
│
├─ For each market:
│   ├─ build_odds_table()
│   ├─ display_odds()
│   │
│   ├─ [--no-llm] print skip message, display_run_metrics(), optional generate_pdf()
│   │
│   ├─ [--deep-research]
│   │   ├─ deep_research_pipeline() → final_report
│   │   ├─ display_deep_research()
│   │   ├─ parse_llm_probabilities(final_report)
│   │   ├─ display_edge_analysis()
│   │   ├─ display_ev_analysis()
│   │   ├─ parse_sentiment(final_report)
│   │   ├─ display_sentiment_analysis()
│   │   ├─ display_run_metrics()
│   │   └─ optional generate_pdf()
│   │
│   └─ [single-pass]
│       ├─ optional web_search_context() (if --web-search)
│       ├─ llm_analysis()
│       ├─ display_llm_analysis()
│       ├─ display_run_metrics()
│       ├─ parse_llm_probabilities()
│       └─ optional generate_pdf()
│
└─ [multi-market + --pdf]
    ├─ display_consolidated()
    └─ generate_consolidated_pdf()
```

**Error handling:**
- API connection/timeout errors: print message and exit with code 1.
- Client is always closed in a `finally` block.
- Web search failures are non-fatal: logged to stderr, pipeline continues.
- Missing LLM keys: exit with a clear message naming the missing environment variable.

---

## 15. Environment Variables

| Variable | Used by | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude provider | Anthropic API authentication |
| `OPENAI_API_KEY` | OpenAI provider | OpenAI API authentication |
| `KIMI_API_KEY` | Kimi provider | Kimi API authentication |
| `LLM_API_KEY` | Custom provider | API key for `--llm custom` endpoint |
| `LLM_BASE_URL` | Custom provider | Base URL for `--llm custom` endpoint |
| `BRAVE_SEARCH_API_KEY` | Web search | Brave Search API authentication (optional) |

Platform-specific trading credentials (if required for data access) should be documented separately in the platform adapter layer, not in this tool.

All variables are loaded from a `.env` file at startup (via `python-dotenv`) with environment taking precedence.

---

## 16. Dependencies

| Package | Purpose |
|---|---|
| `anthropic` | Claude and Anthropic-compatible LLM client |
| `openai` | OpenAI and OpenAI-compatible LLM client |
| `httpx` | Brave Search API HTTP calls |
| `python-dotenv` | `.env` file loading |
| `reportlab` | PDF generation |
| `tzdata` | IANA timezone data (required on Windows for `zoneinfo`) |

The prediction market platform SDK is a separate dependency determined by the target platform.

---

## 17. Adaptation Notes for a New Platform

When implementing this tool against a new prediction market API (e.g. Kalshi), the following components must be replaced or adapted:

| Component | What to replace |
|---|---|
| **API client** | Replace the platform-specific SDK import and client instantiation |
| **`fetch_market_by_slug()`** | Map to the new platform's single-market fetch endpoint and response envelope |
| **`search_and_pick()`** | Map to the new platform's search endpoint; adapt result extraction from the response shape |
| **`search_by_date()`** | Adapt pagination parameters, page-size field names, and date extraction logic to match the new API's pagination and market schema |
| **`_fetch_market_volume()`** | Map to the new platform's order-book or stats endpoint; adapt field names for notional traded and open interest |
| **`build_odds_table()`** | Adapt the two market data shapes to the new platform's outcome/price schema |
| **`_game_date_from_slug()`** | Replace slug-pattern logic with the equivalent identifier convention for the new platform |
| **`_game_has_started()`** | Map `gameStartTime` to the equivalent field in the new platform's market schema |
| **`_market_end_date_est()`** | Map `endDate`/`endTime` to the new platform's settlement timestamp field |
| **API error types** | Replace `APIConnectionError`, `APITimeoutError`, `NotFoundError` with the new SDK's equivalents |
| **Market fields** | Audit all `market.get(...)` calls against the new platform's market object schema |

All LLM, web search, edge/EV, display, and PDF logic is platform-independent and can be used as-is.

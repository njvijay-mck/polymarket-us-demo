"""Odds calculator and winning probability analyser powered by an LLM.

Fetches a Polymarket US market by slug, keyword search, or resolution date (EST),
computes standard betting odds, then asks an LLM to reason about the implied
probabilities. The 4-stage deep research pipeline automatically searches the web
for public team/event metrics before analysing.

Supports multiple LLM providers: Claude (Anthropic), OpenAI, Kimi (Moonshot),
and any OpenAI-compatible endpoint (Ollama, Groq, etc.).

Required env vars (only the one for your chosen provider):
  ANTHROPIC_API_KEY     for Claude (default provider)
  OPENAI_API_KEY        for OpenAI
  KIMI_API_KEY          for Kimi Code (kimi.com)
  LLM_API_KEY           for a custom OpenAI-compatible endpoint
  LLM_BASE_URL          base URL for a custom endpoint

No Polymarket authentication needed (public market data only).

Usage:
    uv run 09_odds_calculator.py btc-100k-2025
    uv run 09_odds_calculator.py btc-100k-2025 --no-llm
    uv run 09_odds_calculator.py btc-100k-2025 --llm kimi
    uv run 09_odds_calculator.py btc-100k-2025 --deep-research
    uv run 09_odds_calculator.py btc-100k-2025 --deep-research --edge-threshold 3
    uv run 09_odds_calculator.py btc-100k-2025 --pdf
    uv run 09_odds_calculator.py btc-100k-2025 --deep-research --pdf report.pdf
    uv run 09_odds_calculator.py --search "bitcoin" --pick 0 --limit 5
    uv run 09_odds_calculator.py --date 2026-02-25 --limit 10 --pick 0
    uv run 09_odds_calculator.py --date 2026-02-25 --llm kimi --deep-research
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date as dt_date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from polymarket_us import PolymarketUS
from polymarket_us import APIConnectionError, APITimeoutError, NotFoundError

load_dotenv()

_EST = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Run metrics  (populated throughout execution, displayed at end)
# ---------------------------------------------------------------------------

@dataclass
class RunMetrics:
    provider: str = ""
    model: str = ""
    pipeline: str = "single-pass"
    agents_run: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)   # {title, url}
    start_time: float = field(default_factory=time.monotonic)

    def elapsed_s(self) -> float:
        return time.monotonic() - self.start_time


@dataclass
class ReportData:
    """Accumulates all output data for a single market; used by generate_pdf()."""
    market: dict
    rows: list[dict]
    analysis_text: str = ""          # single-pass analysis or deep research final report
    llm_probs: list[dict] = field(default_factory=list)
    sentiment: dict | None = None
    metrics: RunMetrics | None = None
    is_deep_research: bool = False
    edge_threshold: float = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_str(value: str | list | None) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _fmt_date(value: str | None) -> str:
    if not value:
        return "-"
    return value.replace("T", " ").split(".")[0].rstrip("Z")


def _status_label(item: dict) -> str:
    if item.get("closed"):
        return "Closed"
    if item.get("archived"):
        return "Archived"
    if item.get("active"):
        return "Active"
    return "Inactive"


def _die(msg: str) -> None:
    print(f"  [LLM ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _market_end_date_est(market: dict) -> dt_date | None:
    """Return the market's resolution date converted to Eastern Time."""
    raw = market.get("endDate") or market.get("endTime")
    if not raw:
        return None
    try:
        clean = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_EST).date()
    except (ValueError, AttributeError):
        return None


def _market_end_et_str(market: dict) -> str | None:
    """Return the market settlement time as a full 'YYYY-MM-DD HH:MM ET' string.

    Showing the time (not just the date) makes it clear when the market
    *settles* vs when a game actually *plays*.  Sports markets often have an
    endDate set to the following afternoon so Polymarket can process results,
    which means a game played on day N may show an ET date of day N+1 if only
    the date portion is displayed.
    """
    raw = market.get("endDate") or market.get("endTime")
    if not raw:
        return None
    try:
        clean = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_EST).strftime("%Y-%m-%d %H:%M ET")
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Odds conversions
# ---------------------------------------------------------------------------

def implied_probability(price: float) -> float:
    """Polymarket price IS the implied probability (0-1 range)."""
    return max(0.0, min(1.0, price))


def decimal_odds(price: float) -> float:
    """Convert implied probability to decimal odds (European format)."""
    if price <= 0:
        return float("inf")
    return round(1.0 / price, 4)


def american_odds(price: float) -> str:
    """Convert implied probability to American moneyline odds."""
    if price <= 0:
        return "N/A"
    if price >= 1:
        return "N/A"
    if price >= 0.5:
        value = round(-(price / (1 - price)) * 100)
        return f"{value:+d}"
    else:
        value = round(((1 - price) / price) * 100)
        return f"+{value}"


def fractional_odds(price: float) -> str:
    """Convert implied probability to simplified fractional odds (UK format)."""
    if price <= 0 or price >= 1:
        return "N/A"
    from math import gcd
    numerator = round((1 - price) * 100)
    denominator = round(price * 100)
    if numerator <= 0 or denominator <= 0:
        return "N/A"
    common = gcd(numerator, denominator)
    return f"{numerator // common}/{denominator // common}"


def overround(prices: list[float]) -> float:
    """Book overround (vig/juice) as a percentage above 100%."""
    total = sum(prices)
    return round((total - 1.0) * 100, 2)


# ---------------------------------------------------------------------------
# Web search  (Brave Search API — requires BRAVE_SEARCH_API_KEY in .env)
# ---------------------------------------------------------------------------

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def web_search_context(question: str, metrics: RunMetrics, max_results: int = 6) -> str:
    """Query Brave Search for publicly available team/event metrics.

    Requires BRAVE_SEARCH_API_KEY in .env (or environment).
    Returns an empty string and prints a warning on any failure so the
    pipeline continues without web context rather than crashing.
    """
    import httpx

    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        print(
            "  [Web Search] BRAVE_SEARCH_API_KEY not set — skipping.\n"
            "  Add it to .env to enable web-enriched analysis.",
            file=sys.stderr,
        )
        return ""

    year  = datetime.now(_EST).year
    query = f"{question} statistics analysis {year}"
    metrics.search_queries.append(query)

    try:
        resp = httpx.get(
            _BRAVE_SEARCH_URL,
            headers={
                "Accept":               "application/json",
                "Accept-Encoding":      "gzip",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": max_results},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(
            f"  [Web Search] Brave Search failed ({exc.__class__.__name__}) — skipping.",
            file=sys.stderr,
        )
        return ""

    results = data.get("web", {}).get("results", [])
    if not results:
        return ""

    lines = [f"## Web Search Results\n\nQuery: `{query}`\n"]
    for i, r in enumerate(results, 1):
        title   = r.get("title",       "").strip()
        url     = r.get("url",         "").strip()
        snippet = r.get("description", "").strip()[:300]
        lines.append(f"**[{i}] {title}**")
        if snippet:
            lines.append(f"> {snippet}")
        if url:
            lines.append(f"Source: {url}")
        lines.append("")
        if title or url:
            metrics.sources.append({"title": title, "url": url})

    return "\n".join(lines)


def social_media_context(question: str, metrics: RunMetrics, max_results: int = 5) -> str:
    """Query Brave Search for X/Twitter and community sentiment signals.

    Makes two targeted queries (social/X sentiment + Reddit/forum discussion).
    Returns a formatted markdown block, or an empty string on any failure.
    Requires BRAVE_SEARCH_API_KEY (same key as web_search_context).
    """
    import httpx

    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return ""

    year = datetime.now(_EST).year
    queries = [
        f"{question} twitter X sentiment public opinion {year}",
        f"{question} reddit community prediction discussion {year}",
    ]

    all_results: list[dict] = []
    for query in queries:
        metrics.search_queries.append(query)
        try:
            resp = httpx.get(
                _BRAVE_SEARCH_URL,
                headers={
                    "Accept":               "application/json",
                    "Accept-Encoding":      "gzip",
                    "X-Subscription-Token": api_key,
                },
                params={"q": query, "count": max_results},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            all_results.extend(data.get("web", {}).get("results", [])[:max_results])
        except Exception as exc:
            print(
                f"  [Social Search] Brave query failed ({exc.__class__.__name__}) — skipping.",
                file=sys.stderr,
            )

    if not all_results:
        return ""

    lines = ["## Social Media & Community Sentiment\n"]
    for i, r in enumerate(all_results, 1):
        title   = r.get("title",       "").strip()
        url     = r.get("url",         "").strip()
        snippet = r.get("description", "").strip()[:300]
        lines.append(f"**[{i}] {title}**")
        if snippet:
            lines.append(f"> {snippet}")
        if url:
            lines.append(f"Source: {url}")
        lines.append("")
        if title or url:
            metrics.sources.append({"title": title, "url": url})

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Market fetching
# ---------------------------------------------------------------------------

def fetch_market_by_slug(client: PolymarketUS, slug: str) -> dict:
    try:
        resp = client.markets.retrieve_by_slug(slug)
        if isinstance(resp, dict) and "market" in resp:
            return resp["market"]
        return resp
    except NotFoundError:
        print(f"\n  Market slug '{slug}' not found.", file=sys.stderr)
        sys.exit(1)


def search_and_pick(
    client: PolymarketUS, query: str, pick: int | None, limit: int
) -> list[dict]:
    """Search for markets, show a numbered list, and return them.

    If *pick* is given, returns only that one market; otherwise returns all.
    """
    fetch_n = max(limit * 2, 20)
    resp = client.search.query({"query": query, "limit": fetch_n})
    markets: list[dict] = []
    if isinstance(resp, dict):
        for event in resp.get("events", []):
            for m in event.get("markets", []):
                markets.append(m)
    elif isinstance(resp, list):
        markets = resp

    markets = markets[:limit]

    if not markets:
        print(f"\n  No markets found for '{query}'.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Found {len(markets)} market(s) for '{query}':\n")
    for i, m in enumerate(markets):
        marker = " ◄" if i == pick else ""
        q   = m.get("question", "Untitled")[:68]
        cat = m.get("category", "-")
        print(f"  [{i}] {q}  [{cat}]{marker}")
    print()

    if pick is not None:
        if pick >= len(markets):
            print(
                f"\n  --pick {pick} out of range; only {len(markets)} market(s) found.",
                file=sys.stderr,
            )
            sys.exit(1)
        return [markets[pick]]

    return markets


def search_by_date(
    client: PolymarketUS,
    date_str: str,
    pick: int | None,
    limit: int,
) -> list[dict]:
    """Find markets resolving on *date_str* (YYYY-MM-DD, Eastern Time).

    Paginates through the markets list until *limit* matches are found or the
    API is exhausted, then displays a numbered menu and returns them.
    If *pick* is given, returns only that one market; otherwise returns all.
    """
    try:
        target_est = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        print(f"\n  Invalid date '{date_str}'. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    # Display what "today in EST" is so the user can sanity-check
    now_est = datetime.now(_EST)
    print(
        f"\n  Searching for markets resolving on {date_str} (EST)  "
        f"[current EST: {now_est.strftime('%Y-%m-%d %H:%M %Z')}] ..."
    )

    candidates: list[dict] = []
    page_size = 100
    offset    = 0
    max_pages = 20

    for _ in range(max_pages):
        if len(candidates) >= limit:
            break
        resp = client.markets.list({"limit": page_size, "offset": offset, "closed": False})
        raw  = resp.get("markets", resp) if isinstance(resp, dict) else resp
        page = raw if isinstance(raw, list) else []
        if not page:
            break
        for m in page:
            end = _market_end_date_est(m)
            if end == target_est:
                candidates.append(m)
                if len(candidates) >= limit:
                    break
        offset += page_size
        if len(page) < page_size:
            break  # API exhausted

    if not candidates:
        print(
            f"\n  No open markets found resolving on {date_str} (EST).\n"
            "  Try a different date or check with --date YYYY-MM-DD.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n  Found {len(candidates)} market(s) resolving on {date_str} (ET):\n")
    for i, m in enumerate(candidates):
        marker  = " ◄" if i == pick else ""
        q       = m.get("question", "Untitled")[:60]
        cat     = m.get("category", "-")
        et_str  = _market_end_et_str(m) or str(_market_end_date_est(m))
        print(f"  [{i}] {q}  [{cat}]  settles {et_str}{marker}")
    print()

    if pick is not None:
        if pick >= len(candidates):
            print(
                f"\n  --pick {pick} out of range; only {len(candidates)} market(s) found.",
                file=sys.stderr,
            )
            sys.exit(1)
        return [candidates[pick]]

    return candidates


# ---------------------------------------------------------------------------
# Odds table builder
# ---------------------------------------------------------------------------

def build_odds_table(market: dict) -> list[dict[str, Any]]:
    """Return a list of outcome dicts with all odds representations.

    Supports two data shapes:
    - Standard markets: outcomes + outcomePrices JSON strings
    - Sports moneyline markets: marketSides list with per-side price + team info
    """
    outcomes   = _parse_json_str(market.get("outcomes"))
    prices_raw = _parse_json_str(market.get("outcomePrices"))

    if outcomes and prices_raw and len(outcomes) == len(prices_raw):
        rows = []
        for outcome, price_str in zip(outcomes, prices_raw):
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                price = 0.0
            prob = implied_probability(price)
            rows.append({
                "outcome":      outcome,
                "price":        price,
                "implied_prob": prob,
                "prob_pct":     round(prob * 100, 2),
                "decimal":      decimal_odds(prob),
                "american":     american_odds(prob),
                "fractional":   fractional_odds(prob),
            })
        return rows

    sides = market.get("marketSides", [])
    if not sides or not isinstance(sides, list):
        return []

    rows = []
    for side in sides:
        if not isinstance(side, dict):
            continue
        team   = side.get("team") or {}
        label  = team.get("name") or side.get("description", "Unknown")
        record = team.get("record", "")
        if record:
            label = f"{label} ({record})"
        try:
            price = float(side.get("price", 0))
        except (ValueError, TypeError):
            price = 0.0
        prob = implied_probability(price)
        rows.append({
            "outcome":      label,
            "price":        price,
            "implied_prob": prob,
            "prob_pct":     round(prob * 100, 2),
            "decimal":      decimal_odds(prob),
            "american":     american_odds(prob),
            "fractional":   fractional_odds(prob),
        })
    return rows


# ---------------------------------------------------------------------------
# Display — odds table
# ---------------------------------------------------------------------------

def display_odds(market: dict, rows: list[dict], verbose: bool) -> None:
    question = market.get("question", "Untitled")
    slug     = market.get("slug", "-")
    status   = _status_label(market)
    category = market.get("category", "-")
    end_date = _fmt_date(market.get("endDate"))
    desc     = market.get("description", "")

    # Show resolution time in both UTC and full ET (date + time).
    # Sports markets often have an endDate set to the next afternoon so
    # Polymarket can process results — showing only the ET date would make a
    # Feb 18 evening game appear to resolve on Feb 19.  The full ET time
    # makes the settlement window explicit.
    resolves_str = f"{end_date} UTC"
    et_str = _market_end_et_str(market)
    if et_str:
        resolves_str += f"  ({et_str})"

    print(f"\n{'=' * 72}")
    print(f"  {question}")
    print(f"{'=' * 72}")
    print(f"  Slug:      {slug}")
    print(f"  Status:    {status}  |  Category: {category}")
    print(f"  Resolves:  {resolves_str}")
    if desc:
        print(f"  Desc:      {desc[:120]}{'...' if len(desc) > 120 else ''}")
    print()

    if verbose:
        print("  [VERBOSE] Raw market JSON:\n")
        lines = json.dumps(market, indent=2, default=str).splitlines()
        for line in lines:
            print(f"    {line}")
        print()

    prices = [r["implied_prob"] for r in rows]
    vig    = overround(prices) if len(prices) > 1 else 0.0

    print(f"  {'Outcome':<20} {'Price':>7}  {'Implied %':>10}  "
          f"{'Decimal':>8}  {'American':>9}  {'Fractional':>11}")
    print(f"  {'-'*20} {'-'*7}  {'-'*10}  {'-'*8}  {'-'*9}  {'-'*11}")

    for r in rows:
        print(
            f"  {r['outcome']:<20} "
            f"${r['price']:>6.4f}  "
            f"{r['prob_pct']:>9.2f}%  "
            f"{r['decimal']:>8.4f}  "
            f"{r['american']:>9}  "
            f"{r['fractional']:>11}"
        )

    print()
    if vig != 0.0:
        print(f"  Book overround (vig): {vig:+.2f}%  "
              f"({'favours bookmaker' if vig > 0 else 'favours bettor'})")
    print()


# ---------------------------------------------------------------------------
# LLM Provider Abstraction
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-haiku-4-5",
    "openai": "gpt-4o",
    "kimi":   "kimi-for-coding",
}

# Kimi's coding endpoint speaks the Anthropic messages API format.
# Use the base path (no /v1 suffix); the Anthropic SDK appends /v1/messages itself.
_KIMI_BASE_URL = "https://api.kimi.com/coding"


class AnthropicLLMClient:
    """Thin wrapper around the Anthropic SDK.

    Supports third-party Anthropic-compatible endpoints (e.g. Kimi Code)
    via the optional base_url parameter.
    """

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        import anthropic  # local import — keeps module loadable without the SDK
        self.model = model
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)

    def chat(self, system: str, user: str, max_tokens: int = 2048) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": user}],
            system=system,
        )
        return msg.content[0].text


class OpenAICompatibleLLMClient:
    """Thin wrapper around the OpenAI SDK (supports any compatible endpoint)."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None) -> None:
        from openai import OpenAI  # local import
        self.model = model
        init_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            init_kwargs["base_url"] = base_url
        self._client = OpenAI(**init_kwargs)

    def chat(self, system: str, user: str, max_tokens: int = 2048) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


def create_llm_client(
    provider: str,
    model:    str | None,
    api_key:  str | None,
    base_url: str | None,
) -> AnthropicLLMClient | OpenAICompatibleLLMClient:
    """Resolve credentials from env / CLI flags and return the right LLM client."""
    resolved_model = model or _DEFAULT_MODELS.get(provider)

    if provider == "claude":
        key = api_key or os.getenv("ANTHROPIC_API_KEY") or ""
        if not key:
            _die("ANTHROPIC_API_KEY not set. Add it to .env or pass --llm-api-key.")
        return AnthropicLLMClient(
            model=resolved_model or _DEFAULT_MODELS["claude"],
            api_key=key,
        )

    if provider == "openai":
        key = api_key or os.getenv("OPENAI_API_KEY") or ""
        if not key:
            _die("OPENAI_API_KEY not set. Add it to .env or pass --llm-api-key.")
        return OpenAICompatibleLLMClient(
            model=resolved_model or _DEFAULT_MODELS["openai"],
            api_key=key,
        )

    if provider == "kimi":
        key = api_key or os.getenv("KIMI_API_KEY") or ""
        if not key:
            _die("KIMI_API_KEY not set. Add it to .env or pass --llm-api-key.")
        # Kimi's /coding/ endpoint speaks the Anthropic messages API,
        # so we route it through AnthropicLLMClient with a custom base_url.
        return AnthropicLLMClient(
            model=resolved_model or _DEFAULT_MODELS["kimi"],
            api_key=key,
            base_url=_KIMI_BASE_URL,
        )

    if provider == "custom":
        key = api_key or os.getenv("LLM_API_KEY") or ""
        url = base_url or os.getenv("LLM_BASE_URL") or ""
        if not key:
            _die("LLM_API_KEY not set. Add it to .env or pass --llm-api-key.")
        if not url:
            _die("LLM_BASE_URL not set. Add it to .env or pass --llm-base-url.")
        if not resolved_model:
            _die("--model is required when using --llm custom.")
        return OpenAICompatibleLLMClient(model=resolved_model, api_key=key, base_url=url)

    _die(f"Unknown LLM provider '{provider}'. Choose: claude, openai, kimi, custom.")
    raise RuntimeError("unreachable")  # for type-checkers


# ---------------------------------------------------------------------------
# Market payload builder  (shared between single-pass and deep research)
# ---------------------------------------------------------------------------

def _build_market_payload(market: dict, rows: list[dict]) -> dict:
    return {
        "question":    market.get("question", "Untitled"),
        "description": market.get("description", ""),
        "category":    market.get("category", "-"),
        "status":      _status_label(market),
        "resolves_utc": _fmt_date(market.get("endDate")),
        "resolves_et":  _market_end_et_str(market) or "-",
        "outcomes": [
            {
                "outcome":       r["outcome"],
                "market_price":  r["price"],
                "implied_prob":  f"{r['prob_pct']:.2f}%",
                "decimal_odds":  r["decimal"],
                "american_odds": r["american"],
                "fractional":    r["fractional"],
            }
            for r in rows
        ],
        "book_overround": f"{overround([r['implied_prob'] for r in rows]):+.2f}%",
    }


# ---------------------------------------------------------------------------
# Single-pass LLM analysis
# ---------------------------------------------------------------------------

_SINGLE_PASS_SYSTEM = """\
You are an expert prediction-market analyst. You receive structured data about
a Polymarket market — the question, current prices, implied probabilities, and
odds in multiple formats, plus any web search context provided. Your job is to:

1. Summarise what the market is about in one sentence.
2. Identify the favourite and underdog(s) with their win probability.
3. Use any web search context (team stats, recent news) to assess whether the
   market pricing seems reasonable, flagging mispricing or interesting skew.
4. Calculate the implied edge (if any) for a contrarian position.
5. Give a concise overall recommendation: "value on YES", "value on NO",
   "fairly priced", or "avoid" — with one sentence of reasoning.

Be factual, concise, and avoid financial advice disclaimers. Format your
response with clear headings using markdown (##).
"""


def llm_analysis(
    market:      dict,
    rows:        list[dict],
    llm_client:  AnthropicLLMClient | OpenAICompatibleLLMClient,
    web_context: str = "",
) -> str:
    """Send market data (+ optional web context) to the LLM and return analysis."""
    payload = _build_market_payload(market, rows)
    web_section = f"\n\n{web_context}" if web_context else ""
    user_message = (
        "Here is the Polymarket market data:\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```"
        + web_section
        + "\n\nPlease provide your analysis."
    )
    return llm_client.chat(system=_SINGLE_PASS_SYSTEM, user=user_message)


def display_llm_analysis(text: str, provider: str) -> None:
    print(f"{'=' * 72}")
    print(f"  LLM Analysis  [{provider}]")
    print(f"{'=' * 72}\n")
    for line in text.splitlines():
        print(f"  {line}")
    print()


# ---------------------------------------------------------------------------
# Deep research pipeline  — step 0 (web search) + 4 LLM stages
# ---------------------------------------------------------------------------

_RESEARCH_SYSTEM = """\
You are an expert research analyst specialising in prediction markets.
Investigate the market question thoroughly using your knowledge AND any web
search results provided.

## Key Facts
Bullet-point list of the most relevant facts, including team/player stats,
recent form, and any figures from the web search results.

## Base Rates
Historical reference-class data or prior probabilities for similar events.

## Recent Context
Recent developments from the web search results that could influence the outcome.

## Risk Factors
Key uncertainties and tail risks.

## Initial Probability Estimate
State a probability estimate for EACH outcome (e.g. "Yes: 60%, No: 40%").

Cite specific statistics or sources from the web search where relevant.
"""

_CRITIQUE_SYSTEM = """\
You are a critical analyst reviewing a research report on a prediction market.
Your task is to rigorously stress-test the analysis before it is acted upon.

## Gaps and Flaws
Specific missing evidence, logical errors, or unsupported claims.

## Cognitive Biases
Identify any availability, recency, anchoring, or confirmation biases.

## Counter-Arguments
The strongest case against the researcher's probability estimate.

## Alternative Scenarios
Plausible scenarios the researcher may have under-weighted.

## Key Open Questions
Questions whose answers would most change the probability estimate.

Be precise and constructive.
"""

_REBUTTAL_SYSTEM = """\
You are a research analyst responding to a critique of your initial report on a
prediction market. Engage with each point carefully and update your analysis
where the critique is valid.

## Point-by-Point Response
Address each critique explicitly — concede where warranted, defend where not.

## Updated Evidence
Any additional facts or reasoning that strengthen or revise your position.

## Revised Probability Estimate
State a REVISED probability for EACH outcome, e.g. "Yes: 65%, No: 35%".
Explain what changed and why.
"""

_CONSOLIDATION_SYSTEM = """\
You are a senior prediction-market analyst producing the final recommendation.
You have access to: initial research (including web search results), a critique,
and the researcher's rebuttal.

## Summary
3-5 sentences synthesising the key factors that drive the outcome, citing
specific statistics or sources where they were material to the analysis.

## Final Probability Estimates
Your final probability for EACH outcome with brief justification.

## Confidence
Overall confidence: "low", "medium", or "high".

## Recommendation
One of: "value on [outcome]", "fairly priced", or "avoid" — with one sentence
of reasoning.

IMPORTANT: You MUST include the following JSON block (one entry per outcome):

```json
[
  {"outcome": "Yes", "llm_probability": 0.62, "confidence": "medium"},
  {"outcome": "No",  "llm_probability": 0.38, "confidence": "medium"}
]
```

Replace the outcome names and probabilities with your actual estimates.
The "outcome" values must exactly match the outcome labels in the market data.
Probabilities must sum to 1.0.

IMPORTANT: You MUST also include a sentiment JSON block based on the social
media and community signals provided:

```sentiment-json
{"overall": "bullish", "score": 0.72, "volume": "high",
 "signals": ["strong X buzz", "Reddit community bullish"],
 "summary": "Community strongly expects Yes resolution"}
```

Where:
- "overall": "bullish", "bearish", or "neutral"
- "score": 0.0 (strongly bearish) to 1.0 (strongly bullish), 0.5 is neutral
- "volume": "low", "moderate", or "high" (social discussion activity)
- "signals": 2-3 short phrases drawn from social/community evidence
- "summary": one sentence summarising the social sentiment

If no social media data was provided, use "neutral", score 0.5, volume "low".
"""


def deep_research_pipeline(
    market:     dict,
    rows:       list[dict],
    llm_client: AnthropicLLMClient | OpenAICompatibleLLMClient,
    metrics:    RunMetrics,
) -> str:
    """Run web search + 4-stage deep research pipeline; return the final report."""
    payload_json = json.dumps(_build_market_payload(market, rows), indent=2)
    market_block = f"## Market Data\n\n```json\n{payload_json}\n```\n\n"

    # Step 0a: Web search
    print("  [0/4] Web Search ...")
    sys.stdout.flush()
    web_context = web_search_context(market.get("question", ""), metrics)
    if web_context:
        metrics.agents_run.append("Web Search")

    # Step 0b: Social media sentiment search
    print("  [0/4] Social Search ...")
    sys.stdout.flush()
    social_context = social_media_context(market.get("question", ""), metrics)
    if social_context:
        metrics.agents_run.append("Social Search")

    context_block = ""
    if web_context:
        context_block += web_context + "\n\n"
    if social_context:
        context_block += social_context + "\n\n"

    research_input = market_block + context_block

    # Stage 1: Research
    print("  [1/4] Running Research Agent ...")
    sys.stdout.flush()
    research = llm_client.chat(
        system=_RESEARCH_SYSTEM,
        user=research_input + "Investigate this market and produce your research report.",
    )
    metrics.agents_run.append("Research")

    # Stage 2: Critique
    print("  [2/4] Running Critique Agent ...")
    sys.stdout.flush()
    critique = llm_client.chat(
        system=_CRITIQUE_SYSTEM,
        user=(
            research_input
            + "## Research Report\n\n" + research
            + "\n\nCritique the above research report."
        ),
    )
    metrics.agents_run.append("Critique")

    # Stage 3: Rebuttal
    print("  [3/4] Running Rebuttal Agent ...")
    sys.stdout.flush()
    rebuttal = llm_client.chat(
        system=_REBUTTAL_SYSTEM,
        user=(
            research_input
            + "## Research Report\n\n" + research
            + "\n\n## Critique\n\n" + critique
            + "\n\nRespond to the critique and refine your analysis."
        ),
    )
    metrics.agents_run.append("Rebuttal")

    # Stage 4: Consolidation
    print("  [4/4] Running Consolidation Agent ...")
    sys.stdout.flush()
    final = llm_client.chat(
        system=_CONSOLIDATION_SYSTEM,
        user=(
            research_input
            + "## Research Report\n\n" + research
            + "\n\n## Critique\n\n" + critique
            + "\n\n## Rebuttal\n\n" + rebuttal
            + "\n\nSynthesise these findings into a final recommendation."
        ),
    )
    metrics.agents_run.append("Consolidation")

    return final


def display_deep_research(final_report: str, provider: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Deep Research Analysis  [{provider}]")
    print(f"{'=' * 72}\n")
    for line in final_report.splitlines():
        print(f"  {line}")
    print()


# ---------------------------------------------------------------------------
# Edge detection & position recommendation
# ---------------------------------------------------------------------------

def parse_llm_probabilities(text: str) -> list[dict]:
    """Extract the JSON probability array from the consolidation stage output."""
    for pattern in (r"```json\s*(\[.*?\])\s*```", r"```\s*(\[.*?\])\s*```"):
        for m in re.finditer(pattern, text, re.DOTALL):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list) and all(
                    "outcome" in item and "llm_probability" in item
                    for item in data
                ):
                    return data
            except json.JSONDecodeError:
                continue
    return []


def parse_sentiment(text: str) -> dict | None:
    """Extract the sentiment JSON object from the consolidation stage output."""
    for m in re.finditer(r"```sentiment-json\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            if "overall" in data and "score" in data:
                return data
        except json.JSONDecodeError:
            continue
    return None


def display_edge_analysis(
    rows:           list[dict],
    llm_probs:      list[dict],
    edge_threshold: float,
) -> None:
    """Print the edge analysis table and recommend a position if edge is found."""
    if not llm_probs:
        print("  [Edge Analysis] Could not parse probability estimates from LLM output.\n")
        return

    llm_map: dict[str, float] = {
        item["outcome"].lower(): float(item["llm_probability"])
        for item in llm_probs
        if "outcome" in item and "llm_probability" in item
    }

    matched: list[tuple[str, float, float]] = []
    for row in rows:
        key   = row["outcome"].lower()
        llm_p = llm_map.get(key)
        if llm_p is None:
            for k, v in llm_map.items():
                if k in key or key in k:
                    llm_p = v
                    break
        if llm_p is not None:
            matched.append((row["outcome"], row["implied_prob"], llm_p))

    if not matched:
        print("  [Edge Analysis] No matching outcomes between market data and LLM output.\n")
        return

    C1, C2, C3, C4 = 22, 12, 12, 12
    inner = C1 + C2 + C3 + C4 + 3

    thresh_txt = f"  EDGE ANALYSIS  (threshold: {edge_threshold:.1f}%)"

    print(f"\n╔{'═' * inner}╗")
    print(f"║{thresh_txt:<{inner}}║")
    print(f"╠{'═'*C1}╦{'═'*C2}╦{'═'*C3}╦{'═'*C4}╣")
    print(
        f"║ {'Outcome':<{C1-1}}"
        f"║ {'Market %':<{C2-1}}"
        f"║ {'LLM Est %':<{C3-1}}"
        f"║ {'Edge':<{C4-1}}║"
    )
    print(f"╠{'═'*C1}╬{'═'*C2}╬{'═'*C3}╬{'═'*C4}╣")

    best: tuple[str, float] | None = None
    for label, market_p, llm_p in matched:
        edge      = (llm_p - market_p) * 100
        arrow     = "\u25b2" if edge > 0 else "\u25bc"
        edge_str  = f"{edge:+.1f}% {arrow}"
        print(
            f"║ {label:<{C1-1}}"
            f"║ {market_p * 100:>8.2f}%  "
            f"║ {llm_p * 100:>8.2f}%  "
            f"║ {edge_str:<{C4-1}}║"
        )
        if best is None or abs(edge) > abs(best[1]):
            best = (label, edge)

    print(f"╚{'═'*C1}╩{'═'*C2}╩{'═'*C3}╩{'═'*C4}╝")

    if best and abs(best[1]) >= edge_threshold:
        direction = "BUY" if best[1] > 0 else "SELL"
        print(
            f"\n  *** RECOMMENDED POSITION: {direction} {best[0].upper()}"
            f"  (edge {best[1]:+.1f}%) ***\n"
        )
    else:
        print(f"\n  No edge detected above threshold ({edge_threshold:.1f}%).\n")


def display_ev_analysis(rows: list[dict], llm_probs: list[dict]) -> None:
    """Print EV per $1 contract for each outcome using LLM probability estimates."""
    if not llm_probs:
        return

    llm_map: dict[str, float] = {
        item["outcome"].lower(): float(item["llm_probability"])
        for item in llm_probs
        if "outcome" in item and "llm_probability" in item
    }

    matched: list[tuple[str, float, float]] = []
    for row in rows:
        label = row.get("outcome", "")
        price = row.get("price", 0.0)
        key   = label.lower()
        if key in llm_map:
            matched.append((label, price, llm_map[key]))
        else:
            for lk, lp in llm_map.items():
                if lk in key or key in lk:
                    matched.append((label, price, lp))
                    break

    if not matched:
        return

    C1, C2, C3, C4 = 22, 12, 12, 20
    inner = C1 + C2 + C3 + C4 + 3

    print(f"\n╔{'═' * inner}╗")
    print(f"║{'  EXPECTED VALUE  (per $1 contract)':<{inner}}║")
    print(f"╠{'═'*C1}╦{'═'*C2}╦{'═'*C3}╦{'═'*C4}╣")
    print(
        f"║ {'Outcome':<{C1-1}}"
        f"║ {'Buy at':<{C2-1}}"
        f"║ {'LLM Prob':<{C3-1}}"
        f"║ {'EV / ROI':<{C4-1}}║"
    )
    print(f"╠{'═'*C1}╬{'═'*C2}╬{'═'*C3}╬{'═'*C4}╣")

    best: tuple[str, float, float, float] | None = None  # label, price, ev, roi
    for label, price, llm_p in matched:
        ev  = llm_p - price
        roi = (ev / price * 100) if price > 0 else 0.0
        arrow = "\u25b2" if ev > 0 else "\u25bc"
        ev_roi_str = f"{ev:+.2f}  {roi:+.1f}% {arrow}"
        print(
            f"║ {label:<{C1-1}}"
            f"║ ${price:>8.4f}  "
            f"║ {llm_p * 100:>7.2f}%  "
            f"║ {ev_roi_str:<{C4-1}}║"
        )
        if best is None or ev > best[2]:
            best = (label, price, ev, roi)

    print(f"╚{'═'*C1}╩{'═'*C2}╩{'═'*C3}╩{'═'*C4}╝")

    if best and best[2] > 0:
        print(
            f"\n  Best EV: BUY {best[0].upper()} at ${best[1]:.2f}"
            f"  \u2192  {best[2]:+.2f} per contract  ({best[3]:+.1f}% ROI)\n"
        )


def display_sentiment_analysis(sentiment: dict | None) -> None:
    """Print a social sentiment summary box from the consolidation stage output."""
    if not sentiment:
        return

    overall = sentiment.get("overall", "neutral").capitalize()
    score   = float(sentiment.get("score", 0.5))
    volume  = sentiment.get("volume", "moderate").capitalize()
    signals = sentiment.get("signals", [])
    summary = sentiment.get("summary", "").strip()

    if score >= 0.70:
        indicator = "\u25b2\u25b2\u25b2  Strongly Bullish"
    elif score >= 0.55:
        indicator = "\u25b2\u25b2   Bullish"
    elif score >= 0.45:
        indicator = "\u2500\u2500\u2500  Neutral"
    elif score >= 0.30:
        indicator = "\u25bc\u25bc   Bearish"
    else:
        indicator = "\u25bc\u25bc\u25bc  Strongly Bearish"

    C1, C2, C3 = 18, 14, 28
    inner = C1 + C2 + C3 + 2

    print(f"\n╔{'═' * inner}╗")
    print(f"║{'  SOCIAL SENTIMENT  (X / web signals)':<{inner}}║")
    print(f"╠{'═'*C1}╦{'═'*C2}╦{'═'*C3}╣")
    print(
        f"║ {'Overall tone':<{C1-1}}"
        f"║ {overall:<{C2-1}}"
        f"║ {indicator:<{C3-1}}║"
    )
    print(
        f"║ {'Score':<{C1-1}}"
        f"║ {score:<{C2-1}.2f}"
        f"║ {'':>{C3-1}}║"
    )
    print(
        f"║ {'Discussion vol':<{C1-1}}"
        f"║ {volume:<{C2-1}}"
        f"║ {'':>{C3-1}}║"
    )
    print(f"╚{'═'*C1}╩{'═'*C2}╩{'═'*C3}╝")

    if signals:
        signals_str = "  |  ".join(f'"{s}"' for s in signals)
        print(f"\n  Signals : {signals_str}")
    if summary:
        print(f"  Summary : {summary}\n")
    else:
        print()


# ---------------------------------------------------------------------------
# Run metrics summary
# ---------------------------------------------------------------------------

def display_run_metrics(metrics: RunMetrics) -> None:
    elapsed = metrics.elapsed_s()
    W = 72

    print(f"\n{'=' * W}")
    print("  RUN SUMMARY")
    print(f"{'=' * W}")

    if metrics.provider:
        model_str = f"  ({metrics.model})" if metrics.model else ""
        print(f"  Provider  : {metrics.provider}{model_str}")

    print(f"  Pipeline  : {metrics.pipeline}")

    if metrics.agents_run:
        chain = " \u2192 ".join(metrics.agents_run)
        # Wrap long agent chains
        if len(chain) > 60:
            print(f"  Agents    : {metrics.agents_run[0]}")
            for a in metrics.agents_run[1:]:
                print(f"              \u2192 {a}")
        else:
            print(f"  Agents    : {chain}")

    print(f"  Duration  : {elapsed:.1f} s")

    if metrics.sources:
        print()
        print(f"  SOURCES REFERENCED  ({len(metrics.sources)} from web search)")
        print(f"  {'-' * (W - 4)}")
        for i, src in enumerate(metrics.sources, 1):
            title = src.get("title", "")
            url   = src.get("url",   "")
            # Truncate title to fit
            display_title = (title[:65] + "...") if len(title) > 68 else title
            if display_title:
                print(f"  [{i:2}] {display_title}")
            if url:
                print(f"        {url}")
    elif metrics.search_queries:
        print(f"  Web search ran ({len(metrics.search_queries)} quer"
              f"{'y' if len(metrics.search_queries) == 1 else 'ies'}) but returned no sources.")
    else:
        print("  Sources   : none  (web search not active)")

    print(f"{'=' * W}\n")


# ---------------------------------------------------------------------------
# PDF report generation
# ---------------------------------------------------------------------------

def _resolve_pdf_path(arg: str, market: dict) -> str:
    """Return the PDF output path: user-supplied name, or auto-generated."""
    if arg and arg != "auto":
        return arg
    slug = market.get("slug", "market")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{ts}.pdf"


def _md_to_paragraphs(text: str, styles: Any) -> list:
    """Convert simple LLM markdown output into a list of reportlab Flowables."""
    from reportlab.platypus import Paragraph, Spacer
    from reportlab.lib.units import cm

    body   = styles["body"]
    h2     = styles["h2"]
    bullet = styles["bullet"]

    flowables: list = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flowables.append(Spacer(1, 0.15 * cm))
            continue
        if stripped.startswith("## "):
            flowables.append(Spacer(1, 0.2 * cm))
            flowables.append(Paragraph(stripped[3:], h2))
            flowables.append(Spacer(1, 0.1 * cm))
        elif stripped.startswith("### "):
            flowables.append(Spacer(1, 0.15 * cm))
            flowables.append(Paragraph(f"<b>{stripped[4:]}</b>", body))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:]
            content = content.replace("**", "<b>", 1).replace("**", "</b>", 1)
            flowables.append(Paragraph(f"• {content}", bullet))
        else:
            content = stripped
            # Convert inline **bold** markers
            import re as _re
            content = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", content)
            flowables.append(Paragraph(content, body))
    return flowables


def generate_pdf(report: ReportData, output_path: str) -> None:
    """Render a polished PDF report for one market using reportlab Platypus."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Table, TableStyle,
            Spacer, HRFlowable, KeepTogether,
        )
    except ImportError:
        print(
            "\n  [PDF] reportlab is not installed.\n"
            "  Run: uv sync   (it is listed in pyproject.toml)",
            file=sys.stderr,
        )
        return

    # ── Color palette ────────────────────────────────────────────────────────
    C_NAVY   = colors.HexColor("#1a3a5c")   # dark navy — main headers
    C_BLUE   = colors.HexColor("#2e6da4")   # medium blue — sub-headers
    C_GOLD   = colors.HexColor("#c8922a")   # gold — highlights / accents
    C_ALT    = colors.HexColor("#eef3f8")   # light blue — alternating rows
    C_LGRAY  = colors.HexColor("#d8e2ec")   # light gray — borders
    C_GREEN  = colors.HexColor("#1e7e34")   # green — positive edge
    C_RED    = colors.HexColor("#c0392b")   # red — negative edge
    C_TEXT   = colors.HexColor("#1a1a2e")   # near-black body text
    C_WHITE  = colors.white

    # ── Paragraph styles ─────────────────────────────────────────────────────
    base   = getSampleStyleSheet()
    normal = base["Normal"]

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, parent=normal, **kw)

    styles = {
        "title":    ps("title",   fontSize=18, textColor=C_WHITE,  fontName="Helvetica-Bold",
                        spaceAfter=2, leading=22),
        "subtitle": ps("subtitle", fontSize=9,  textColor=C_LGRAY,  fontName="Helvetica",
                        spaceAfter=0),
        "h1":       ps("h1",      fontSize=12, textColor=C_WHITE,  fontName="Helvetica-Bold",
                        spaceBefore=4, spaceAfter=4, leading=16),
        "h2":       ps("h2",      fontSize=10, textColor=C_NAVY,   fontName="Helvetica-Bold",
                        spaceBefore=6, spaceAfter=3),
        "body":     ps("body",    fontSize=9,  textColor=C_TEXT,   fontName="Helvetica",
                        leading=13, spaceAfter=2),
        "bullet":   ps("bullet",  fontSize=9,  textColor=C_TEXT,   fontName="Helvetica",
                        leading=13, leftIndent=12, spaceAfter=1),
        "small":    ps("small",   fontSize=7.5, textColor=colors.HexColor("#555577"),
                        fontName="Helvetica"),
        "bold":     ps("bold",    fontSize=9,  textColor=C_TEXT,   fontName="Helvetica-Bold"),
        "recommend":ps("recommend", fontSize=10, textColor=C_NAVY, fontName="Helvetica-Bold",
                        spaceBefore=4, spaceAfter=4),
    }

    # ── Common table helpers ──────────────────────────────────────────────────
    def header_row_style(row: int, bg: Any = C_NAVY) -> list:
        return [
            ("BACKGROUND", (0, row), (-1, row), bg),
            ("TEXTCOLOR",  (0, row), (-1, row), C_WHITE),
            ("FONTNAME",   (0, row), (-1, row), "Helvetica-Bold"),
            ("FONTSIZE",   (0, row), (-1, row), 8.5),
            ("TOPPADDING", (0, row), (-1, row), 5),
            ("BOTTOMPADDING", (0, row), (-1, row), 5),
        ]

    def body_row_style(start: int, count: int) -> list:
        cmds = [
            ("FONTNAME",   (0, start), (-1, start + count - 1), "Helvetica"),
            ("FONTSIZE",   (0, start), (-1, start + count - 1), 8.5),
            ("TOPPADDING", (0, start), (-1, start + count - 1), 4),
            ("BOTTOMPADDING", (0, start), (-1, start + count - 1), 4),
        ]
        for r in range(count):
            if r % 2 == 1:
                cmds.append(("BACKGROUND", (0, start + r), (-1, start + r), C_ALT))
        return cmds

    def grid_style() -> list:
        return [
            ("GRID",       (0, 0), (-1, -1), 0.4, C_LGRAY),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [C_WHITE, C_ALT]),
        ]

    # ── Document setup ────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
        title="Polymarket Odds Analysis",
        author="09_odds_calculator.py",
    )
    W = doc.width  # usable width
    story: list = []

    market   = report.market
    rows     = report.rows
    metrics  = report.metrics
    question = market.get("question", "Untitled")
    slug     = market.get("slug", "-")
    category = market.get("category", "-")
    status   = _status_label(market)
    end_date = _fmt_date(market.get("endDate"))
    desc     = market.get("description", "")
    gen_ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── SECTION 1: Title banner ───────────────────────────────────────────────
    banner_data = [
        [Paragraph("POLYMARKET ODDS ANALYSIS REPORT", styles["title"])],
        [Paragraph(f"Generated {gen_ts}  ·  {slug}", styles["subtitle"])],
    ]
    banner_table = Table(banner_data, colWidths=[W])
    banner_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_NAVY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
        ("TOPPADDING",    (0, 0), (0, 0),  10),
        ("BOTTOMPADDING", (0, 0), (0, 0),  2),
        ("TOPPADDING",    (0, 1), (0, 1),  2),
        ("BOTTOMPADDING", (0, 1), (0, 1),  10),
        ("LINEBELOW",     (0, -1), (-1, -1), 3, C_GOLD),
    ]))
    story.append(banner_table)
    story.append(Spacer(1, 0.4 * cm))

    # ── SECTION 2: Market info card ───────────────────────────────────────────
    # Show the full ET datetime so settlement time is unambiguous.  Sports
    # markets commonly have an endDate set to the following afternoon; showing
    # only the date can make a Feb 18 game look like a Feb 19 event.
    resolves_str = f"{end_date} UTC"
    et_str = _market_end_et_str(market)
    if et_str:
        resolves_str += f"  ({et_str})"

    info_rows: list[list] = [
        [Paragraph("MARKET INFORMATION", styles["h1"])],
    ]
    info_header = Table(info_rows, colWidths=[W])
    info_header.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_BLUE),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    story.append(info_header)

    def info_cell(label: str, value: str) -> list:
        return [
            Paragraph(f"<b>{label}</b>", styles["small"]),
            Paragraph(value or "—", styles["body"]),
        ]

    detail_data = [
        info_cell("Question",  question),
        info_cell("Slug",      slug),
        info_cell("Category",  f"{category}  ·  Status: {status}"),
        info_cell("Resolves",  resolves_str),
    ]
    if desc:
        short_desc = (desc[:180] + "…") if len(desc) > 180 else desc
        detail_data.append(info_cell("Description", short_desc))

    detail_table = Table(detail_data, colWidths=[2.5 * cm, W - 2.5 * cm])
    detail_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#f5f8fc")),
        ("FONTNAME",     (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.3, C_LGRAY),
        ("LINEAFTER",    (0, 0), (0, -1),  0.3, C_LGRAY),
        ("LINEBELOW",    (0, -1), (-1, -1), 1.5, C_BLUE),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── SECTION 3: Odds table ─────────────────────────────────────────────────
    prices   = [r["implied_prob"] for r in rows]
    vig      = overround(prices) if len(prices) > 1 else 0.0
    vig_note = f"Book overround (vig): {vig:+.2f}%  " \
               f"({'favours bookmaker' if vig > 0 else 'favours bettor' if vig < 0 else 'zero vig'})"

    odds_header = Table([[Paragraph("ODDS TABLE", styles["h1"])]], colWidths=[W])
    odds_header.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_BLUE),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    story.append(odds_header)

    col_w = [W * f for f in [0.24, 0.13, 0.14, 0.13, 0.17, 0.19]]
    odds_data: list[list] = [
        [Paragraph(h, styles["bold"]) for h in
         ["Outcome", "Price", "Implied %", "Decimal", "American", "Fractional"]],
    ]
    for r in rows:
        odds_data.append([
            Paragraph(r["outcome"], styles["body"]),
            Paragraph(f"${r['price']:.4f}", styles["body"]),
            Paragraph(f"{r['prob_pct']:.2f}%", styles["body"]),
            Paragraph(f"{r['decimal']:.4f}", styles["body"]),
            Paragraph(r["american"], styles["body"]),
            Paragraph(r["fractional"], styles["body"]),
        ])
    odds_table = Table(odds_data, colWidths=col_w, repeatRows=1)
    odds_table.setStyle(TableStyle(
        header_row_style(0) + body_row_style(1, len(rows)) + grid_style() + [
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0,  -1), "LEFT"),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ]
    ))
    story.append(odds_table)
    story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(vig_note, styles["small"]))
    story.append(Spacer(1, 0.5 * cm))

    # ── SECTION 4: LLM Analysis ───────────────────────────────────────────────
    if report.analysis_text and metrics:
        section_title = "DEEP RESEARCH ANALYSIS" if report.is_deep_research else "LLM ANALYSIS"
        provider_info = f"{metrics.provider}  ·  {metrics.model}" if metrics.model else metrics.provider
        analysis_header = Table(
            [[Paragraph(f"{section_title}  [{provider_info}]", styles["h1"])]],
            colWidths=[W],
        )
        analysis_header.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_BLUE),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ]))
        story.append(analysis_header)
        story.append(Spacer(1, 0.2 * cm))
        story.extend(_md_to_paragraphs(report.analysis_text, styles))
        story.append(Spacer(1, 0.4 * cm))

    # ── SECTION 5: Edge Analysis ──────────────────────────────────────────────
    if report.is_deep_research and report.llm_probs:
        llm_map: dict[str, float] = {
            item["outcome"].lower(): float(item["llm_probability"])
            for item in report.llm_probs
        }
        matched: list[tuple[str, float, float]] = []
        for row in rows:
            key   = row["outcome"].lower()
            llm_p = llm_map.get(key)
            if llm_p is None:
                for k, v in llm_map.items():
                    if k in key or key in k:
                        llm_p = v
                        break
            if llm_p is not None:
                matched.append((row["outcome"], row["implied_prob"], llm_p))

        if matched:
            thr = report.edge_threshold
            edge_header = Table(
                [[Paragraph(f"EDGE ANALYSIS  (threshold: {thr:.1f}%)", styles["h1"])]],
                colWidths=[W],
            )
            edge_header.setStyle(TableStyle([
                ("BACKGROUND",   (0, 0), (-1, -1), C_NAVY),
                ("LEFTPADDING",  (0, 0), (-1, -1), 10),
                ("TOPPADDING",   (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
            ]))
            story.append(edge_header)

            ecol_w = [W * f for f in [0.34, 0.22, 0.22, 0.22]]
            edge_data: list[list] = [
                [Paragraph(h, styles["bold"]) for h in
                 ["Outcome", "Market %", "LLM Est %", "Edge"]],
            ]
            best_label, best_edge = "", 0.0
            for label, market_p, llm_p in matched:
                edge     = (llm_p - market_p) * 100
                arrow    = "▲" if edge > 0 else "▼"
                edge_str = f"{edge:+.1f}% {arrow}"
                edge_data.append([
                    Paragraph(label, styles["body"]),
                    Paragraph(f"{market_p * 100:.2f}%", styles["body"]),
                    Paragraph(f"{llm_p * 100:.2f}%", styles["body"]),
                    Paragraph(edge_str, styles["body"]),
                ])
                if abs(edge) > abs(best_edge):
                    best_label, best_edge = label, edge

            edge_table = Table(edge_data, colWidths=ecol_w)
            edge_cmds = header_row_style(0, C_NAVY) + grid_style() + [
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0,  -1), "LEFT"),
                ("FONTNAME",   (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",   (0, 1), (-1, -1), 8.5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ]
            # Colour positive/negative edge cells
            for i, (_, market_p, llm_p) in enumerate(matched):
                edge = (llm_p - market_p) * 100
                clr  = C_GREEN if edge > 0 else C_RED
                edge_cmds.append(("TEXTCOLOR", (3, i + 1), (3, i + 1), clr))
                edge_cmds.append(("FONTNAME",  (3, i + 1), (3, i + 1), "Helvetica-Bold"))
            edge_table.setStyle(TableStyle(edge_cmds))
            story.append(edge_table)
            story.append(Spacer(1, 0.15 * cm))

            if abs(best_edge) >= thr:
                direction = "BUY" if best_edge > 0 else "SELL"
                rec_text  = (
                    f"★  RECOMMENDED POSITION: {direction} {best_label.upper()}"
                    f"  (edge {best_edge:+.1f}%)"
                )
                rec_box = Table([[Paragraph(rec_text, styles["recommend"])]], colWidths=[W])
                rec_box.setStyle(TableStyle([
                    ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#fef9ee")),
                    ("LEFTPADDING",  (0, 0), (-1, -1), 12),
                    ("TOPPADDING",   (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
                    ("LINEBELOW",    (0, -1), (-1, -1), 2, C_GOLD),
                    ("LINEBEFORE",   (0, 0), (0, -1),  4, C_GOLD),
                ]))
                story.append(rec_box)
            else:
                story.append(Paragraph(
                    f"No edge detected above threshold ({thr:.1f}%).",
                    styles["small"],
                ))
            story.append(Spacer(1, 0.5 * cm))

    # ── SECTION 6: EV Analysis ────────────────────────────────────────────────
    if report.is_deep_research and report.llm_probs:
        llm_map = {
            item["outcome"].lower(): float(item["llm_probability"])
            for item in report.llm_probs
        }
        ev_matched: list[tuple[str, float, float]] = []
        for row in rows:
            label = row.get("outcome", "")
            price = row.get("price", 0.0)
            key   = label.lower()
            llm_p = llm_map.get(key)
            if llm_p is None:
                for lk, lv in llm_map.items():
                    if lk in key or key in lk:
                        llm_p = lv
                        break
            if llm_p is not None:
                ev_matched.append((label, price, llm_p))

        if ev_matched:
            ev_header = Table(
                [[Paragraph("EXPECTED VALUE  (per $1 contract)", styles["h1"])]],
                colWidths=[W],
            )
            ev_header.setStyle(TableStyle([
                ("BACKGROUND",   (0, 0), (-1, -1), C_NAVY),
                ("LEFTPADDING",  (0, 0), (-1, -1), 10),
                ("TOPPADDING",   (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
            ]))
            story.append(ev_header)

            evcol_w = [W * f for f in [0.30, 0.20, 0.20, 0.30]]
            ev_data: list[list] = [
                [Paragraph(h, styles["bold"]) for h in
                 ["Outcome", "Buy at", "LLM Prob", "EV / ROI"]],
            ]
            best_ev_label, best_ev = "", 0.0
            best_ev_price = 0.0
            for label, price, llm_p in ev_matched:
                ev  = llm_p - price
                roi = (ev / price * 100) if price > 0 else 0.0
                arrow    = "▲" if ev > 0 else "▼"
                ev_str   = f"{ev:+.3f}  {roi:+.1f}% {arrow}"
                ev_data.append([
                    Paragraph(label, styles["body"]),
                    Paragraph(f"${price:.4f}", styles["body"]),
                    Paragraph(f"{llm_p * 100:.2f}%", styles["body"]),
                    Paragraph(ev_str, styles["body"]),
                ])
                if ev > best_ev:
                    best_ev_label, best_ev, best_ev_price = label, ev, price

            ev_table = Table(ev_data, colWidths=evcol_w)
            ev_cmds = header_row_style(0, C_NAVY) + grid_style() + [
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0,  -1), "LEFT"),
                ("FONTNAME",   (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",   (0, 1), (-1, -1), 8.5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ]
            for i, (_, price, llm_p) in enumerate(ev_matched):
                ev  = llm_p - price
                clr = C_GREEN if ev > 0 else C_RED
                ev_cmds.append(("TEXTCOLOR", (3, i + 1), (3, i + 1), clr))
                ev_cmds.append(("FONTNAME",  (3, i + 1), (3, i + 1), "Helvetica-Bold"))
            ev_table.setStyle(TableStyle(ev_cmds))
            story.append(ev_table)

            if best_ev > 0:
                roi = (best_ev / best_ev_price * 100) if best_ev_price > 0 else 0.0
                story.append(Spacer(1, 0.1 * cm))
                story.append(Paragraph(
                    f"Best EV: BUY {best_ev_label.upper()} at ${best_ev_price:.4f}"
                    f"  →  {best_ev:+.3f} per contract  ({roi:+.1f}% ROI)",
                    styles["small"],
                ))
            story.append(Spacer(1, 0.5 * cm))

    # ── SECTION 7: Social Sentiment ───────────────────────────────────────────
    if report.sentiment:
        sent = report.sentiment
        overall  = sent.get("overall", "neutral").capitalize()
        score    = float(sent.get("score", 0.5))
        volume   = sent.get("volume", "moderate").capitalize()
        signals  = sent.get("signals", [])
        summary  = sent.get("summary", "").strip()

        if score >= 0.70:
            indicator = "▲▲▲  Strongly Bullish"
            ind_color = C_GREEN
        elif score >= 0.55:
            indicator = "▲▲   Bullish"
            ind_color = C_GREEN
        elif score >= 0.45:
            indicator = "───  Neutral"
            ind_color = colors.HexColor("#888888")
        elif score >= 0.30:
            indicator = "▼▼   Bearish"
            ind_color = C_RED
        else:
            indicator = "▼▼▼  Strongly Bearish"
            ind_color = C_RED

        sent_header = Table(
            [[Paragraph("SOCIAL SENTIMENT  (X / Reddit signals)", styles["h1"])]],
            colWidths=[W],
        )
        sent_header.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_BLUE),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ]))
        story.append(sent_header)

        scol_w = [W * f for f in [0.25, 0.20, 0.55]]
        sent_data = [
            [Paragraph("<b>Overall tone</b>", styles["small"]),
             Paragraph(overall, styles["body"]),
             Paragraph(indicator, styles["body"])],
            [Paragraph("<b>Score</b>", styles["small"]),
             Paragraph(f"{score:.2f} / 1.0", styles["body"]),
             Paragraph("", styles["body"])],
            [Paragraph("<b>Discussion</b>", styles["small"]),
             Paragraph(volume, styles["body"]),
             Paragraph("", styles["body"])],
        ]
        if signals:
            sigs_str = "  ·  ".join(f'"{s}"' for s in signals)
            sent_data.append([
                Paragraph("<b>Signals</b>", styles["small"]),
                Paragraph(sigs_str, styles["body"]),
                Paragraph("", styles["body"]),
            ])
        if summary:
            sent_data.append([
                Paragraph("<b>Summary</b>", styles["small"]),
                Paragraph(summary, styles["body"]),
                Paragraph("", styles["body"]),
            ])

        sent_table = Table(sent_data, colWidths=scol_w)
        sent_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#f5f8fc")),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.3, C_LGRAY),
            ("LINEAFTER",    (0, 0), (1, -1),  0.3, C_LGRAY),
            ("TEXTCOLOR",    (2, 0), (2, 0),   ind_color),
            ("FONTNAME",     (2, 0), (2, 0),   "Helvetica-Bold"),
            ("LINEBELOW",    (0, -1), (-1, -1), 1.5, C_BLUE),
        ]))
        story.append(sent_table)
        story.append(Spacer(1, 0.5 * cm))

    # ── SECTION 8: Run Summary ────────────────────────────────────────────────
    if metrics:
        run_header = Table(
            [[Paragraph("RUN SUMMARY", styles["h1"])]],
            colWidths=[W],
        )
        run_header.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), C_NAVY),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("TOPPADDING",   (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ]))
        story.append(run_header)

        elapsed = metrics.elapsed_s()
        agents  = " → ".join(metrics.agents_run) if metrics.agents_run else "—"
        run_data = [
            ["Provider",  f"{metrics.provider}  ({metrics.model})" if metrics.model else metrics.provider],
            ["Pipeline",  metrics.pipeline],
            ["Agents",    agents],
            ["Duration",  f"{elapsed:.1f} s"],
        ]
        run_table = Table(
            [[Paragraph(f"<b>{k}</b>", styles["small"]), Paragraph(v, styles["body"])]
             for k, v in run_data],
            colWidths=[2.5 * cm, W - 2.5 * cm],
        )
        run_table.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#f5f8fc")),
            ("FONTSIZE",     (0, 0), (-1, -1), 9),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("LINEBELOW",    (0, 0), (-1, -1), 0.3, C_LGRAY),
            ("LINEAFTER",    (0, 0), (0, -1),  0.3, C_LGRAY),
        ]))
        story.append(run_table)

        if metrics.sources:
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph(
                f"<b>Sources referenced  ({len(metrics.sources)} from web search)</b>",
                styles["bold"],
            ))
            story.append(Spacer(1, 0.1 * cm))
            src_data = []
            for i, src in enumerate(metrics.sources, 1):
                title = src.get("title", "")
                url   = src.get("url", "")
                label = (title[:70] + "…") if len(title) > 70 else title
                src_data.append([
                    Paragraph(f"[{i}]", styles["small"]),
                    Paragraph(
                        f"{label}<br/><font color='#2e6da4'><i>{url}</i></font>"
                        if url else label,
                        styles["small"],
                    ),
                ])
            src_table = Table(src_data, colWidths=[0.6 * cm, W - 0.6 * cm])
            src_table.setStyle(TableStyle([
                ("FONTSIZE",     (0, 0), (-1, -1), 7.5),
                ("TOPPADDING",   (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
                ("LEFTPADDING",  (0, 0), (-1, -1), 4),
                ("LINEBELOW",    (0, 0), (-1, -1), 0.3, C_LGRAY),
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(src_table)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5 * cm))
    footer_table = Table(
        [[Paragraph(
            "Generated by 09_odds_calculator.py  ·  Polymarket US API demo  ·  "
            "For informational purposes only",
            styles["small"],
        )]],
        colWidths=[W],
    )
    footer_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), C_NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, -1), C_LGRAY),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LINEABOVE",    (0, 0), (-1, -1), 3, C_GOLD),
    ]))
    story.append(footer_table)

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(story)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Odds calculator + LLM probability analysis for a Polymarket market",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run 09_odds_calculator.py btc-100k-2025
  uv run 09_odds_calculator.py btc-100k-2025 --no-llm
  uv run 09_odds_calculator.py btc-100k-2025 --llm kimi
  uv run 09_odds_calculator.py btc-100k-2025 --deep-research --edge-threshold 3
  uv run 09_odds_calculator.py --search "NBA finals" --limit 5 --pick 0
  uv run 09_odds_calculator.py --date 2026-03-15 --limit 10 --pick 0
  uv run 09_odds_calculator.py --date 2026-03-15 --llm kimi --deep-research
""",
    )

    # Source: slug | --search | --date  (mutually exclusive, one required)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "slug",
        nargs="?",
        help="Market slug to analyse (e.g. btc-100k-2025)",
    )
    source.add_argument(
        "--search",
        metavar="QUERY",
        help="Search for a market by keyword",
    )
    source.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Find markets resolving on this date (Eastern Time)",
    )

    parser.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="N",
        help="Analyse only result N (0-indexed) when using --search or --date; omit to analyse all",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="Max markets to show when using --search or --date (default: 10)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM analysis and show odds only",
    )

    # LLM provider flags
    parser.add_argument(
        "--llm",
        default="claude",
        metavar="PROVIDER",
        choices=["claude", "openai", "kimi", "custom"],
        help="LLM provider: claude (default), openai, kimi, custom",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help=(
            "Model ID to use "
            "(defaults: claude→claude-haiku-4-5, openai→gpt-4o, kimi→kimi-for-coding)"
        ),
    )
    parser.add_argument(
        "--llm-base-url",
        default=None,
        metavar="URL",
        help="Base URL for a custom OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        metavar="KEY",
        help="API key override (otherwise read from env var for the chosen provider)",
    )

    # Pipeline flags
    parser.add_argument(
        "--deep-research",
        action="store_true",
        help=(
            "Enable 4-stage deep research pipeline with web search "
            "(Web Search → Research → Critique → Rebuttal → Consolidation)"
        ),
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        help="Run a web search to enrich single-pass analysis (always on for --deep-research)",
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=5.0,
        metavar="N",
        help="Minimum %% edge to flag a recommended position (default: 5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw market JSON",
    )
    parser.add_argument(
        "--pdf",
        nargs="?",
        const="auto",
        default=None,
        metavar="FILENAME",
        help=(
            "Save a formatted PDF report (auto-named <slug>_<timestamp>.pdf if no "
            "filename given). PDFs are git-ignored and intended for local reading."
        ),
    )

    args = parser.parse_args()

    pm_client = PolymarketUS()

    try:
        # -- Fetch market list -------------------------------------------------
        if args.slug:
            print(f"Fetching market: {args.slug} ...")
            markets = [fetch_market_by_slug(pm_client, args.slug)]
        elif args.search:
            markets = search_and_pick(pm_client, args.search, args.pick, args.limit)
        else:
            markets = search_by_date(pm_client, args.date, args.pick, args.limit)

        # Create LLM client once (reused across all markets)
        llm_client = None
        if not args.no_llm:
            llm_client = create_llm_client(
                provider=args.llm,
                model=args.model,
                api_key=args.llm_api_key,
                base_url=args.llm_base_url,
            )

        # -- Analyse each market -----------------------------------------------
        for idx, market in enumerate(markets):
            metrics = RunMetrics()

            if len(markets) > 1:
                W = 72
                print(f"\n{'━' * W}")
                print(
                    f"  MARKET {idx + 1} / {len(markets)}: "
                    f"{market.get('question', 'Untitled')}"
                )
                print(f"{'━' * W}\n")

            rows = build_odds_table(market)
            if not rows:
                print(
                    f"\n  No outcome price data available for market [{idx}].",
                    file=sys.stderr,
                )
                continue

            display_odds(market, rows, verbose=args.verbose)

            # -- LLM analysis --------------------------------------------------
            if args.no_llm:
                metrics.pipeline = "none (--no-llm)"
                print("  (LLM analysis skipped — run without --no-llm to enable)\n")
                display_run_metrics(metrics)
                if args.pdf:
                    pdf_path = _resolve_pdf_path(args.pdf, market)
                    generate_pdf(
                        ReportData(market=market, rows=rows, metrics=metrics),
                        pdf_path,
                    )
                    print(f"  PDF saved: {pdf_path}\n")
                continue

            metrics.provider = args.llm
            metrics.model    = llm_client.model

            if args.deep_research:
                metrics.pipeline = "deep-research"
                print(f"  Running deep research pipeline [{args.llm}] ...\n")
                final_report = deep_research_pipeline(market, rows, llm_client, metrics)
                display_deep_research(final_report, provider=args.llm)
                llm_probs = parse_llm_probabilities(final_report)
                display_edge_analysis(rows, llm_probs, edge_threshold=args.edge_threshold)
                display_ev_analysis(rows, llm_probs)
                sentiment = parse_sentiment(final_report)
                display_sentiment_analysis(sentiment)
                display_run_metrics(metrics)
                if args.pdf:
                    pdf_path = _resolve_pdf_path(args.pdf, market)
                    generate_pdf(
                        ReportData(
                            market=market,
                            rows=rows,
                            analysis_text=final_report,
                            llm_probs=llm_probs,
                            sentiment=sentiment,
                            metrics=metrics,
                            is_deep_research=True,
                            edge_threshold=args.edge_threshold,
                        ),
                        pdf_path,
                    )
                    print(f"  PDF saved: {pdf_path}\n")
            else:
                metrics.pipeline = "single-pass"
                web_context = ""
                if args.web_search:
                    print("  [Web Search] Fetching public metrics ...")
                    sys.stdout.flush()
                    web_context = web_search_context(
                        market.get("question", ""), metrics
                    )
                    if web_context:
                        metrics.agents_run.append("Web Search")

                metrics.agents_run.append("Analysis")
                print(f"  Asking {args.llm} for analysis ...\n")
                analysis = llm_analysis(market, rows, llm_client, web_context=web_context)
                display_llm_analysis(analysis, provider=args.llm)
                display_run_metrics(metrics)
                if args.pdf:
                    pdf_path = _resolve_pdf_path(args.pdf, market)
                    generate_pdf(
                        ReportData(
                            market=market,
                            rows=rows,
                            analysis_text=analysis,
                            metrics=metrics,
                            is_deep_research=False,
                        ),
                        pdf_path,
                    )
                    print(f"  PDF saved: {pdf_path}\n")

    except APIConnectionError as e:
        print(f"\nConnection error: {e.message}", file=sys.stderr)
        sys.exit(1)
    except APITimeoutError:
        print("\nRequest timed out. Try again later.", file=sys.stderr)
        sys.exit(1)
    finally:
        pm_client.close()


if __name__ == "__main__":
    main()

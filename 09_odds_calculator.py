"""Odds calculator and winning probability analyser powered by an LLM.

Fetches a Polymarket US market by slug, computes standard betting odds
representations from the live prices, then asks Claude to reason about
the implied probabilities and provide a plain-English analysis.

Requires:
- ANTHROPIC_API_KEY  in .env (or environment) for the LLM analysis step
- No Polymarket authentication needed (public market data only)

Usage:
    uv run 09_odds_calculator.py btc-100k-2025
    uv run 09_odds_calculator.py btc-100k-2025 --no-llm          # odds only, skip LLM
    uv run 09_odds_calculator.py btc-100k-2025 --model claude-opus-4-5
    uv run 09_odds_calculator.py btc-100k-2025 --verbose          # raw JSON
    uv run 09_odds_calculator.py --search "bitcoin" --pick 0      # search then analyse
"""

import argparse
import json
import os
import sys
from typing import Any

import anthropic
from dotenv import load_dotenv

from polymarket_us import PolymarketUS
from polymarket_us import APIConnectionError, APITimeoutError, NotFoundError

load_dotenv()


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
        # Favourite — negative American odds
        value = round(-(price / (1 - price)) * 100)
        return f"{value:+d}"
    else:
        # Underdog — positive American odds
        value = round(((1 - price) / price) * 100)
        return f"+{value}"


def fractional_odds(price: float) -> str:
    """Convert implied probability to simplified fractional odds (UK format)."""
    if price <= 0 or price >= 1:
        return "N/A"
    from math import gcd
    # Work in hundredths for simplicity
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
# Market fetching
# ---------------------------------------------------------------------------

def fetch_market_by_slug(client: PolymarketUS, slug: str) -> dict:
    try:
        resp = client.markets.retrieve_by_slug(slug)
        # API wraps single-market responses under a "market" key
        if isinstance(resp, dict) and "market" in resp:
            return resp["market"]
        return resp
    except NotFoundError:
        print(f"\n  Market slug '{slug}' not found.", file=sys.stderr)
        sys.exit(1)


def search_and_pick(client: PolymarketUS, query: str, pick: int) -> dict:
    """Search for markets and return the one at position *pick* (0-indexed)."""
    resp = client.search.query({"query": query, "limit": 20})
    markets: list[dict] = []
    if isinstance(resp, dict):
        for event in resp.get("events", []):
            for m in event.get("markets", []):
                markets.append(m)
    elif isinstance(resp, list):
        markets = resp

    if not markets:
        print(f"\n  No markets found for '{query}'.", file=sys.stderr)
        sys.exit(1)

    if pick >= len(markets):
        print(
            f"\n  --pick {pick} out of range; only {len(markets)} market(s) found.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Found {len(markets)} market(s) for '{query}'. Analysing #{pick}: "
          f"{markets[pick].get('question', 'Untitled')}\n")
    return markets[pick]


# ---------------------------------------------------------------------------
# Odds table builder
# ---------------------------------------------------------------------------

def build_odds_table(market: dict) -> list[dict[str, Any]]:
    """Return a list of outcome dicts with all odds representations.

    Supports two data shapes:
    - Standard markets: outcomes + outcomePrices JSON strings
    - Sports moneyline markets: marketSides list with per-side price + team info
    """
    outcomes = _parse_json_str(market.get("outcomes"))
    prices_raw = _parse_json_str(market.get("outcomePrices"))

    # --- Primary path: outcomes + outcomePrices both present and aligned ------
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

    # --- Fallback: sports markets store prices in marketSides -----------------
    sides = market.get("marketSides", [])
    if not sides or not isinstance(sides, list):
        return []

    rows = []
    for side in sides:
        if not isinstance(side, dict):
            continue
        # Outcome label: use team name if available, else side description
        team = side.get("team") or {}
        label = team.get("name") or side.get("description", "Unknown")
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
# Display
# ---------------------------------------------------------------------------

def display_odds(market: dict, rows: list[dict], verbose: bool) -> None:
    question  = market.get("question", "Untitled")
    slug      = market.get("slug", "-")
    status    = _status_label(market)
    category  = market.get("category", "-")
    end_date  = _fmt_date(market.get("endDate"))
    desc      = market.get("description", "")

    print(f"\n{'=' * 72}")
    print(f"  {question}")
    print(f"{'=' * 72}")
    print(f"  Slug:      {slug}")
    print(f"  Status:    {status}  |  Category: {category}")
    print(f"  Resolves:  {end_date}")
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
    vig = overround(prices) if len(prices) > 1 else 0.0

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
# LLM analysis
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert prediction-market analyst. You receive structured data about
a Polymarket market — the question, current prices, implied probabilities, and
odds in multiple formats. Your job is to:

1. Summarise what the market is about in one sentence.
2. Identify the favourite and underdog(s) with their win probability.
3. Comment on whether the market pricing seems reasonable given general knowledge,
   flagging any mispricing or interesting skew.
4. Calculate the implied edge (if any) for a contrarian position.
5. Give a concise overall recommendation: "value on YES", "value on NO",
   "fairly priced", or "avoid" — with one sentence of reasoning.

Be factual, concise, and avoid financial advice disclaimers. Format your
response with clear headings using markdown (##).
"""


def llm_analysis(market: dict, rows: list[dict], model: str) -> str:
    """Send market data to Claude and return the analysis text."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            "  [LLM SKIPPED] ANTHROPIC_API_KEY not set in .env or environment.\n"
            "  Add it to run the Claude analysis."
        )

    # Build a compact payload for the prompt
    payload = {
        "question":    market.get("question", "Untitled"),
        "description": market.get("description", ""),
        "category":    market.get("category", "-"),
        "status":      _status_label(market),
        "resolves":    _fmt_date(market.get("endDate")),
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

    user_message = (
        "Here is the Polymarket market data:\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        "Please provide your analysis."
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": user_message}],
        system=SYSTEM_PROMPT,
    )
    return message.content[0].text


def display_llm_analysis(text: str) -> None:
    print(f"{'=' * 72}")
    print("  Claude AI Analysis")
    print(f"{'=' * 72}\n")
    # Indent each line for consistent formatting
    for line in text.splitlines():
        print(f"  {line}")
    print()


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
  uv run 09_odds_calculator.py btc-100k-2025 --model claude-opus-4-5
  uv run 09_odds_calculator.py --search "bitcoin" --pick 0
  uv run 09_odds_calculator.py --search "NBA finals" --pick 1 --no-llm
""",
    )

    # Source: slug or search
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "slug",
        nargs="?",
        help="Market slug to analyse (e.g. btc-100k-2025)",
    )
    source.add_argument(
        "--search",
        metavar="QUERY",
        help="Search for a market by keyword instead of providing a slug",
    )

    parser.add_argument(
        "--pick",
        type=int,
        default=0,
        metavar="N",
        help="Which result to analyse when using --search (0-indexed, default: 0)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the Claude LLM analysis and show odds only",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5",
        help="Anthropic model to use (default: claude-haiku-4-5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw market JSON",
    )

    args = parser.parse_args()

    pm_client = PolymarketUS()

    try:
        # -- Fetch market ------------------------------------------------------
        if args.slug:
            print(f"Fetching market: {args.slug} ...")
            market = fetch_market_by_slug(pm_client, args.slug)
        else:
            print(f"Searching for: \"{args.search}\" ...")
            market = search_and_pick(pm_client, args.search, args.pick)

        # -- Build & display odds ---------------------------------------------
        rows = build_odds_table(market)
        if not rows:
            print("\n  No outcome price data available for this market.", file=sys.stderr)
            sys.exit(1)

        display_odds(market, rows, verbose=args.verbose)

        # -- LLM analysis -----------------------------------------------------
        if args.no_llm:
            print("  (LLM analysis skipped — use without --no-llm to enable)\n")
        else:
            print("  Asking Claude for analysis ...\n")
            analysis = llm_analysis(market, rows, model=args.model)
            display_llm_analysis(analysis)

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

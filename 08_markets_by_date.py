"""Fetch Polymarket US markets that resolve on a specific date, with optional keyword search.

No authentication required. Demonstrates:
- Filtering markets by end/resolution date
- Combining date filtering with keyword search
- Client-side date range matching (exact date or date range)
- Paginated fetching to find enough matching markets

Usage:
    uv run 08_markets_by_date.py --date 2025-03-01
    uv run 08_markets_by_date.py --date 2025-03-01 --search "bitcoin"
    uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31
    uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31 --search "NBA"
    uv run 08_markets_by_date.py --date 2025-06-30 --search "election" --limit 20
    uv run 08_markets_by_date.py --date 2025-03-01 --status all
    uv run 08_markets_by_date.py --date 2025-03-01 --verbose
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone


from polymarket_us import PolymarketUS
from polymarket_us import APIConnectionError, APITimeoutError


# -- Helpers -------------------------------------------------------------------

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


def _format_date(value: str | None) -> str:
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


def _format_outcomes(market: dict) -> str:
    outcomes = _parse_json_str(market.get("outcomes"))
    prices = _parse_json_str(market.get("outcomePrices"))
    if outcomes and prices and len(outcomes) == len(prices):
        parts = [f"{o}: ${p}" for o, p in zip(outcomes, prices)]
        return " | ".join(parts)
    if outcomes:
        return ", ".join(str(o) for o in outcomes)
    return "-"


def _indent(text: str, prefix: str = "       ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _parse_date_arg(value: str) -> date:
    """Parse a date string in YYYY-MM-DD format."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Use YYYY-MM-DD format (e.g., 2025-03-01)."
        )


def _market_end_date(market: dict) -> date | None:
    """Extract the resolution/end date from a market dict."""
    raw = market.get("endDate") or market.get("endTime")
    if not raw:
        return None
    try:
        # Handle ISO strings: "2025-03-01T00:00:00Z" or "2025-03-01"
        raw_clean = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw_clean)
        return dt.date()
    except (ValueError, AttributeError):
        return None


def _matches_search(market: dict, query: str) -> bool:
    """Case-insensitive substring search across key market text fields."""
    q = query.lower()
    fields = [
        market.get("question", ""),
        market.get("description", ""),
        market.get("category", ""),
        market.get("slug", ""),
    ]
    # Also search nested event titles
    for event in market.get("events", []):
        if isinstance(event, dict):
            fields.append(event.get("title", ""))
    return any(q in (f or "").lower() for f in fields)


# -- Display -------------------------------------------------------------------

def display_markets(
    markets: list[dict],
    date_from: date,
    date_to: date,
    search: str | None,
    verbose: bool,
) -> None:
    date_range_str = (
        str(date_from) if date_from == date_to else f"{date_from} to {date_to}"
    )
    search_str = f'  |  Search: "{search}"' if search else ""

    print(f"\n{'=' * 72}")
    print(f"  Markets resolving: {date_range_str}{search_str}")
    print(f"  Found: {len(markets)} market(s)")
    print(f"{'=' * 72}\n")

    if not markets:
        print("  No markets found matching the criteria.\n")
        return

    if verbose and markets:
        print("  [VERBOSE] First market raw JSON:\n")
        print(_indent(json.dumps(markets[0], indent=2, default=str), "    "))
        print()

    for i, market in enumerate(markets, 1):
        question = market.get("question", "Untitled")
        slug = market.get("slug", "-")
        status = _status_label(market)
        category = market.get("category", "-")
        market_type = market.get("marketType", "-")
        outcomes_str = _format_outcomes(market)
        end_date = _market_end_date(market)
        start = _format_date(market.get("startDate"))
        end = _format_date(market.get("endDate"))
        game_start = _format_date(market.get("gameStartTime"))
        updated = _format_date(market.get("updatedAt"))
        description = market.get("description", "")
        sports_type = market.get("sportsMarketType", "")
        ep3_status = market.get("ep3Status", "")

        print(f"  {i:>3}. {question}")
        print(f"       Slug:       {slug}")
        print(f"       Status:     {status}  |  Category: {category}")

        type_parts = [f"Type: {market_type}"]
        if sports_type:
            type_parts.append(f"Sports: {sports_type}")
        if ep3_status:
            type_parts.append(f"EP3: {ep3_status}")
        print(f"       Market:     {' | '.join(type_parts)}")

        print(f"       Outcomes:   [{outcomes_str}]")

        # Highlight resolution date
        print(f"       Resolves:   {end_date or '-'}")

        date_parts = [f"Start: {start}"]
        if end and end != "-":
            date_parts.append(f"End: {end}")
        if game_start and game_start != "-":
            date_parts.append(f"Game Start: {game_start}")
        print(f"       Dates:      {' | '.join(date_parts)}")

        if updated and updated != "-":
            print(f"       Updated:    {updated}")

        if description:
            desc_preview = description[:120] + ("..." if len(description) > 120 else "")
            print(f"       Desc:       {desc_preview}")

        # Market sides
        sides = market.get("marketSides", [])
        if sides and isinstance(sides, list):
            print(f"       Sides ({len(sides)}):")
            for side in sides:
                if not isinstance(side, dict):
                    continue
                side_desc = side.get("description", "-")
                is_long = side.get("long", None)
                direction = "LONG" if is_long else ("SHORT" if is_long is False else "")
                team = side.get("team", {})
                if isinstance(team, dict) and team:
                    t_name = team.get("name", "-")
                    t_abbr = team.get("abbreviation", "")
                    label = f"{t_name} ({t_abbr})" if t_abbr else t_name
                    t_record = team.get("record", "")
                    detail = f"  [Record: {t_record}]" if t_record else ""
                    print(f"         {direction:>5} : {side_desc} -> {label}{detail}")
                else:
                    print(f"         {direction:>5} : {side_desc}")

        # Parent events
        nested_events = market.get("events", [])
        if nested_events:
            print(f"       Events ({len(nested_events)}):")
            for ev in nested_events:
                if isinstance(ev, dict):
                    print(f"         - {ev.get('title', '-')} (slug: {ev.get('slug', '-')})")

        print()


# -- Fetching and filtering ----------------------------------------------------

def fetch_markets_by_date(
    client: PolymarketUS,
    date_from: date,
    date_to: date,
    search: str | None,
    limit: int,
    status: str,
) -> list[dict]:
    """
    Fetch markets from the API in pages and filter to those whose endDate
    falls within [date_from, date_to], and (optionally) match *search*.

    Strategy:
      - Use the search endpoint when a keyword is provided (richer results).
      - Fall back to the markets list endpoint otherwise.
      - Paginate until we have *limit* matches or the API returns nothing more.
    """
    matched: list[dict] = []
    page_size = max(limit * 4, 100)   # Over-fetch to absorb date filtering
    offset = 0
    max_pages = 20                    # Safety cap on pagination

    for _ in range(max_pages):
        if len(matched) >= limit:
            break

        # -- Choose endpoint ---------------------------------------------------
        if search:
            resp = client.search.query({
                "query": search,
                "limit": page_size,
                "offset": offset,
            })
            # Search returns {"events": [...]} with nested markets
            raw_markets = _extract_markets_from_search(resp)
        else:
            params: dict = {"limit": page_size, "offset": offset}
            if status == "open":
                params["closed"] = False
            elif status == "closed":
                params["closed"] = True
            resp = client.markets.list(params)
            raw = resp.get("markets", resp) if isinstance(resp, dict) else resp
            raw_markets = raw if isinstance(raw, list) else []

        if not raw_markets:
            break  # No more data

        # -- Filter by date (and status for search path) ----------------------
        for m in raw_markets:
            if len(matched) >= limit:
                break

            # Status filter (search path doesn't support server-side filtering)
            if search:
                if status == "open" and m.get("closed"):
                    continue
                if status == "closed" and not m.get("closed"):
                    continue

            end = _market_end_date(m)
            if end is None:
                continue
            if not (date_from <= end <= date_to):
                continue

            matched.append(m)

        offset += page_size

    return matched[:limit]


def _extract_markets_from_search(resp: dict | list) -> list[dict]:
    """Flatten markets out of a search response (events -> markets)."""
    markets: list[dict] = []
    if isinstance(resp, dict):
        for event in resp.get("events", []):
            if isinstance(event, dict):
                for m in event.get("markets", []):
                    if isinstance(m, dict):
                        # Attach parent event info for display
                        m.setdefault("events", [{"title": event.get("title"), "slug": event.get("slug")}])
                        markets.append(m)
    elif isinstance(resp, list):
        markets = resp
    return markets


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find Polymarket US markets by resolution date, with optional keyword search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run 08_markets_by_date.py --date 2025-03-01
  uv run 08_markets_by_date.py --date 2025-03-01 --search "bitcoin"
  uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31
  uv run 08_markets_by_date.py --date-from 2025-03-01 --date-to 2025-03-31 --search "NBA"
  uv run 08_markets_by_date.py --date 2025-06-30 --search "election" --limit 20 --status all
""",
    )

    # Date arguments (mutually exclusive groups)
    date_group = parser.add_argument_group("Date filter (required: use --date OR --date-from/--date-to)")
    date_group.add_argument(
        "--date",
        type=_parse_date_arg,
        metavar="YYYY-MM-DD",
        help="Exact resolution date",
    )
    date_group.add_argument(
        "--date-from",
        type=_parse_date_arg,
        metavar="YYYY-MM-DD",
        help="Start of resolution date range (inclusive)",
    )
    date_group.add_argument(
        "--date-to",
        type=_parse_date_arg,
        metavar="YYYY-MM-DD",
        help="End of resolution date range (inclusive)",
    )

    parser.add_argument(
        "--search",
        type=str,
        metavar="TEXT",
        help="Optional keyword filter applied to question, description, category, and event title",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of markets to display (default: 10)",
    )
    parser.add_argument(
        "--status",
        choices=["open", "closed", "all"],
        default="open",
        help="Market status filter: open (default), closed, or all",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print raw JSON for the first matching market",
    )

    args = parser.parse_args()

    # -- Validate date arguments -----------------------------------------------
    if args.date and (args.date_from or args.date_to):
        parser.error("Use either --date OR --date-from/--date-to, not both.")

    if not args.date and not args.date_from and not args.date_to:
        parser.error("Provide a date filter: --date YYYY-MM-DD  OR  --date-from / --date-to")

    if args.date:
        date_from = date_to = args.date
    else:
        date_from = args.date_from or args.date_to
        date_to = args.date_to or args.date_from
        if date_from > date_to:
            parser.error("--date-from must be on or before --date-to")

    # -- Run -------------------------------------------------------------------
    client = PolymarketUS()

    date_range_str = (
        str(date_from) if date_from == date_to else f"{date_from} to {date_to}"
    )
    search_str = f' + search="{args.search}"' if args.search else ""
    print(f"Fetching markets resolving on {date_range_str}{search_str} (status={args.status})...")

    try:
        markets = fetch_markets_by_date(
            client,
            date_from=date_from,
            date_to=date_to,
            search=args.search,
            limit=args.limit,
            status=args.status,
        )
        display_markets(
            markets,
            date_from=date_from,
            date_to=date_to,
            search=args.search,
            verbose=args.verbose,
        )

    except APIConnectionError as e:
        print(f"\nConnection error: {e.message}", file=sys.stderr)
        sys.exit(1)
    except APITimeoutError:
        print("\nRequest timed out. Try again later.", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()

"""Search Polymarket US events and markets by keyword.

No authentication required. Demonstrates:
- Full-text search via the SDK
- CLI argument parsing
- Rich, detailed search results including nested markets, outcomes, sides, teams
- Formatted search results

Usage:
    uv run 02_search_markets.py "bitcoin"
    uv run 02_search_markets.py "presidential election"
    uv run 02_search_markets.py "super bowl" --limit 5
    uv run 02_search_markets.py "NBA" --verbose
"""

import argparse
import json
import sys

from polymarket_us import PolymarketUS
from polymarket_us import APIConnectionError, APITimeoutError, BadRequestError


# -- Formatting helpers --------------------------------------------------------

def _parse_json_str(value: str | list | None) -> list:
    """Parse a field that may be a JSON-encoded string or already a list."""
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
    """Shorten an ISO timestamp to a readable date-time string."""
    if not value:
        return "-"
    return value.replace("T", " ").split(".")[0].rstrip("Z")


def _status_label(item: dict) -> str:
    """Derive a human-readable status from boolean flags."""
    if item.get("closed"):
        return "Closed"
    if item.get("archived"):
        return "Archived"
    if item.get("active"):
        return "Active"
    return "Inactive"


def _format_outcomes(market: dict) -> str:
    """Build an outcome string like 'Yes: $0.55 | No: $0.45'."""
    outcomes = _parse_json_str(market.get("outcomes"))
    prices = _parse_json_str(market.get("outcomePrices"))
    if outcomes and prices and len(outcomes) == len(prices):
        parts = [f"{o}: ${p}" for o, p in zip(outcomes, prices)]
        return " | ".join(parts)
    if outcomes:
        return ", ".join(str(o) for o in outcomes)
    return "-"


def _indent(text: str, prefix: str = "       ") -> str:
    """Indent every line of *text* by *prefix*."""
    return "\n".join(prefix + line for line in text.splitlines())


# -- Display detailed market ---------------------------------------------------

def display_market_detail(m: dict, indent: str = "         ") -> None:
    """Print rich details for a single market dict (nested inside an event)."""
    m_question = m.get("question", "Untitled")
    m_slug = m.get("slug", "-")
    m_status = _status_label(m)
    m_type = m.get("marketType", "-")
    sports_type = m.get("sportsMarketType", "")
    sports_type_v2 = m.get("sportsMarketTypeV2", "")
    ep3_status = m.get("ep3Status", "")
    outcomes_str = _format_outcomes(m)
    start = _format_date(m.get("startDate"))
    end = _format_date(m.get("endDate"))
    game_start = _format_date(m.get("gameStartTime"))
    updated = _format_date(m.get("updatedAt"))
    description = m.get("description", "")

    print(f"{indent}- {m_question}")
    print(f"{indent}  Slug: {m_slug}  |  {m_status}  |  Type: {m_type}")

    # Sport type details
    sport_parts = []
    if sports_type:
        sport_parts.append(f"Sports: {sports_type}")
    if sports_type_v2 and sports_type_v2 != sports_type:
        sport_parts.append(f"SportsV2: {sports_type_v2}")
    if ep3_status:
        sport_parts.append(f"EP3: {ep3_status}")
    if sport_parts:
        print(f"{indent}  {' | '.join(sport_parts)}")

    # Outcomes
    print(f"{indent}  Outcomes: [{outcomes_str}]")

    # Dates
    date_parts = []
    if start and start != "-":
        date_parts.append(f"Start: {start}")
    if end and end != "-":
        date_parts.append(f"End: {end}")
    if game_start and game_start != "-":
        date_parts.append(f"Game Start: {game_start}")
    if date_parts:
        print(f"{indent}  Dates: {' | '.join(date_parts)}")

    if updated and updated != "-":
        print(f"{indent}  Updated: {updated}")

    # Description (truncated)
    if description:
        desc_preview = description[:100] + ("..." if len(description) > 100 else "")
        print(f"{indent}  Desc: {desc_preview}")

    # Market sides (team breakdown)
    sides = m.get("marketSides", [])
    if sides and isinstance(sides, list):
        print(f"{indent}  Sides ({len(sides)}):")
        for side in sides:
            if not isinstance(side, dict):
                continue
            side_desc = side.get("description", "-")
            is_long = side.get("long", None)
            direction = "LONG" if is_long else ("SHORT" if is_long is False else "")
            participant_id = side.get("participantId", "")

            team = side.get("team", {})
            if isinstance(team, dict) and team:
                t_name = team.get("name", "-")
                t_abbr = team.get("abbreviation", "")
                t_league = team.get("league", "")
                t_record = team.get("record", "")
                t_seed = team.get("seed", "")

                label = t_name
                if t_abbr:
                    label = f"{t_name} ({t_abbr})"

                detail = []
                if t_league:
                    detail.append(f"League: {t_league}")
                if t_record:
                    detail.append(f"Record: {t_record}")
                if t_seed:
                    detail.append(f"Seed: {t_seed}")

                line = f"{indent}    {direction:>5} : {side_desc} -> {label}"
                if detail:
                    line += f"  [{', '.join(detail)}]"
                print(line)
            else:
                pid_str = f"  (participant: {participant_id})" if participant_id else ""
                print(f"{indent}    {direction:>5} : {side_desc}{pid_str}")


# -- Display search results ----------------------------------------------------

def display_results(results: dict | list, query: str, verbose: bool = False) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Search Results for: \"{query}\"")
    print(f"{'=' * 72}\n")

    if isinstance(results, dict):
        events = results.get("events", [])

        if verbose and events:
            print(f"  [VERBOSE] First event raw JSON:\n")
            print(_indent(json.dumps(events[0], indent=2, default=str), "    "))
            print()

        if events:
            print(f"  Found {len(events)} event(s)\n")

            for i, event in enumerate(events, 1):
                title = event.get("title", "Untitled")
                slug = event.get("slug", "-")
                status = _status_label(event)
                category = event.get("category", "-")
                start = _format_date(event.get("startDate"))
                end = _format_date(event.get("endDate"))
                created = _format_date(event.get("createdAt"))
                updated = _format_date(event.get("updatedAt"))
                start_time = _format_date(event.get("startTime"))
                series_slug = event.get("seriesSlug", "")
                period = event.get("period", "")
                elapsed = event.get("elapsed", "")
                game_id = event.get("gameId", "")
                sportradar_id = event.get("sportradarGameId", "")
                ticker = event.get("ticker", "")
                description = event.get("description", "")

                # Header
                print(f"  {i:>3}. {title}")
                print(f"       Slug:       {slug}")
                print(f"       Status:     {status}  |  Category: {category}")

                # Dates
                date_parts = [f"Start: {start}"]
                if end and end != "-":
                    date_parts.append(f"End: {end}")
                if start_time and start_time != "-":
                    date_parts.append(f"Game Start: {start_time}")
                print(f"       Dates:      {' | '.join(date_parts)}")

                if created and created != "-":
                    print(f"       Created:    {created}  |  Updated: {updated}")

                # Optional identifiers
                ids = []
                if series_slug:
                    ids.append(f"Series: {series_slug}")
                if ticker:
                    ids.append(f"Ticker: {ticker}")
                if game_id:
                    ids.append(f"Game ID: {game_id}")
                if sportradar_id:
                    ids.append(f"Sportradar: {sportradar_id}")
                if period:
                    ids.append(f"Period: {period}")
                if elapsed:
                    ids.append(f"Elapsed: {elapsed}")
                if ids:
                    print(f"       IDs:        {' | '.join(ids)}")

                # Description (truncated)
                if description:
                    desc_preview = description[:120] + ("..." if len(description) > 120 else "")
                    print(f"       Desc:       {desc_preview}")

                # Participants
                participants = event.get("participants", [])
                if participants and isinstance(participants, list):
                    print(f"       Participants ({len(participants)}):")
                    for p in participants:
                        if isinstance(p, dict):
                            p_name = p.get("name", "-")
                            p_abbr = p.get("abbreviation", "")
                            p_league = p.get("league", "")
                            p_record = p.get("record", "")
                            p_seed = p.get("seed", "")
                            parts = [p_name]
                            if p_abbr:
                                parts[0] = f"{p_name} ({p_abbr})"
                            detail = []
                            if p_league:
                                detail.append(f"League: {p_league}")
                            if p_record:
                                detail.append(f"Record: {p_record}")
                            if p_seed:
                                detail.append(f"Seed: {p_seed}")
                            line = f"         - {parts[0]}"
                            if detail:
                                line += f"  [{', '.join(detail)}]"
                            print(line)

                # Nested markets - full detail
                nested_markets = event.get("markets", [])
                if nested_markets:
                    print(f"       Markets ({len(nested_markets)}):")
                    for m in nested_markets:
                        display_market_detail(m)

                print()
        else:
            print("  No results found.\n")

    elif isinstance(results, list):
        if not results:
            print("  No results found.\n")
            return
        for i, item in enumerate(results, 1):
            title = item.get("title", item.get("question", item.get("name", "Untitled")))
            slug = item.get("slug", "-")
            status = _status_label(item)
            category = item.get("category", "-")
            print(f"  {i:>3}. {title}")
            print(f"       Slug: {slug}  |  {status}  |  Category: {category}")
            outcomes = _format_outcomes(item)
            if outcomes != "-":
                print(f"       Outcomes: [{outcomes}]")
            print()
    else:
        print(f"  Raw response: {results}")


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Search Polymarket US markets")
    parser.add_argument("query", type=str, help="Search query string")
    parser.add_argument("--limit", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--verbose", action="store_true", help="Print raw JSON for the first result")
    args = parser.parse_args()

    client = PolymarketUS()

    try:
        print(f"Searching for \"{args.query}\"...")
        results = client.search.query({"query": args.query, "limit": args.limit})
        display_results(results, args.query, verbose=args.verbose)

    except BadRequestError as e:
        print(f"\nBad request: {e.message}", file=sys.stderr)
        sys.exit(1)
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

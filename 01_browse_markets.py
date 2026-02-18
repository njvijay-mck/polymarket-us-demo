"""Browse active events and markets on Polymarket US.

No authentication required. Demonstrates:
- Listing events with filtering and pagination
- Listing markets with full detail
- Participant and team information
- Market sides breakdown
- Formatted console output

Usage:
    uv run 01_browse_markets.py
    uv run 01_browse_markets.py --limit 20
    uv run 01_browse_markets.py --events-only
    uv run 01_browse_markets.py --markets-only
    uv run 01_browse_markets.py --status open     # default: only non-closed markets
    uv run 01_browse_markets.py --status closed   # only closed/resolved markets
    uv run 01_browse_markets.py --status all      # no filtering on closed status
    uv run 01_browse_markets.py --verbose          # show raw JSON for first item
"""

import argparse
import json
import sys

from polymarket_us import PolymarketUS
from polymarket_us import APIConnectionError, APITimeoutError


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
    # Keep just date + time portion, drop fractional seconds & Z
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


# -- Display events ------------------------------------------------------------

def display_events(events: list[dict], limit: int) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Active Events (showing up to {limit})")
    print(f"{'=' * 72}\n")

    if not events:
        print("  No active events found.")
        return

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
                    p_logo = p.get("logo", "")
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

        # Nested markets summary
        nested_markets = event.get("markets", [])
        if nested_markets:
            print(f"       Markets ({len(nested_markets)}):")
            for m in nested_markets:
                m_question = m.get("question", "Untitled")
                m_slug = m.get("slug", "-")
                m_status = _status_label(m)
                m_type = m.get("marketType", "-")
                m_outcomes = _format_outcomes(m)
                print(f"         - {m_question}")
                print(f"           Slug: {m_slug}  |  {m_status}  |  Type: {m_type}")
                print(f"           Outcomes: [{m_outcomes}]")

                # Market sides (team breakdown)
                sides = m.get("marketSides", [])
                if sides and isinstance(sides, list):
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
                            t_league = team.get("league", "")
                            t_record = team.get("record", "")
                            t_seed = team.get("seed", "")
                            t_parts = [f"{t_name}"]
                            if t_abbr:
                                t_parts[0] = f"{t_name} ({t_abbr})"
                            t_detail = []
                            if t_league:
                                t_detail.append(f"League: {t_league}")
                            if t_record:
                                t_detail.append(f"Record: {t_record}")
                            if t_seed:
                                t_detail.append(f"Seed: {t_seed}")
                            team_str = t_parts[0]
                            if t_detail:
                                team_str += f"  [{', '.join(t_detail)}]"
                            print(f"           Side: {side_desc} ({direction}) -> {team_str}")
                        else:
                            print(f"           Side: {side_desc} ({direction})")

        print()


# -- Display markets -----------------------------------------------------------

def display_markets(markets: list[dict], limit: int) -> None:
    print(f"\n{'=' * 72}")
    print(f"  Markets (showing up to {limit})")
    print(f"{'=' * 72}\n")

    if not markets:
        print("  No markets found.")
        return

    for i, market in enumerate(markets, 1):
        question = market.get("question", "Untitled")
        slug = market.get("slug", "-")
        status = _status_label(market)
        category = market.get("category", "-")
        market_type = market.get("marketType", "-")
        sports_type = market.get("sportsMarketType", "")
        sports_type_v2 = market.get("sportsMarketTypeV2", "")
        outcomes_str = _format_outcomes(market)
        start = _format_date(market.get("startDate"))
        end = _format_date(market.get("endDate"))
        game_start = _format_date(market.get("gameStartTime"))
        updated = _format_date(market.get("updatedAt"))
        ep3_status = market.get("ep3Status", "")
        description = market.get("description", "")
        manual_activation = market.get("manualActivation", None)

        # Header
        print(f"  {i:>3}. {question}")
        print(f"       Slug:       {slug}")
        print(f"       Status:     {status}  |  Category: {category}")

        # Type info
        type_parts = [f"Type: {market_type}"]
        if sports_type:
            type_parts.append(f"Sports: {sports_type}")
        if sports_type_v2 and sports_type_v2 != sports_type:
            type_parts.append(f"SportsV2: {sports_type_v2}")
        if ep3_status:
            type_parts.append(f"EP3: {ep3_status}")
        print(f"       Market:     {' | '.join(type_parts)}")

        # Outcomes
        print(f"       Outcomes:   [{outcomes_str}]")

        # Dates
        date_parts = [f"Start: {start}"]
        if end and end != "-":
            date_parts.append(f"End: {end}")
        if game_start and game_start != "-":
            date_parts.append(f"Game Start: {game_start}")
        print(f"       Dates:      {' | '.join(date_parts)}")

        if updated and updated != "-":
            print(f"       Updated:    {updated}")

        if manual_activation is not None:
            print(f"       Manual Act: {manual_activation}")

        # Description (truncated)
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
                participant_id = side.get("participantId", "")

                team = side.get("team", {})
                if isinstance(team, dict) and team:
                    t_name = team.get("name", "-")
                    t_abbr = team.get("abbreviation", "")
                    t_league = team.get("league", "")
                    t_record = team.get("record", "")
                    t_seed = team.get("seed", "")
                    t_logo = team.get("logo", "")

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

                    line = f"         {direction:>5} : {side_desc} -> {label}"
                    if detail:
                        line += f"  [{', '.join(detail)}]"
                    print(line)
                else:
                    pid_str = f"  (participant: {participant_id})" if participant_id else ""
                    print(f"         {direction:>5} : {side_desc}{pid_str}")

        # Nested events (from markets endpoint, events may be nested)
        nested_events = market.get("events", [])
        if nested_events and isinstance(nested_events, list):
            print(f"       Parent Events ({len(nested_events)}):")
            for ev in nested_events:
                if isinstance(ev, dict):
                    ev_title = ev.get("title", "-")
                    ev_slug = ev.get("slug", "-")
                    print(f"         - {ev_title} (slug: {ev_slug})")

        print()


# -- Main ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Browse Polymarket US events and markets")
    parser.add_argument("--limit", type=int, default=10, help="Number of items to fetch (default: 10)")
    parser.add_argument("--events-only", action="store_true", help="Only show events")
    parser.add_argument("--markets-only", action="store_true", help="Only show markets")
    parser.add_argument(
        "--status",
        choices=["open", "closed", "all"],
        default="open",
        help="Filter by market status: open (default), closed, or all",
    )
    parser.add_argument("--verbose", action="store_true", help="Print raw JSON for the first item")
    args = parser.parse_args()

    client = PolymarketUS()

    # Over-fetch (3x) so client-side filtering still returns enough items
    fetch_limit = args.limit if args.status == "all" else args.limit * 3

    closed_param: dict = {}
    if args.status == "open":
        closed_param = {"closed": False}
    elif args.status == "closed":
        closed_param = {"closed": True}

    def _filter_by_status(items: list[dict]) -> list[dict]:
        if args.status == "open":
            filtered = [item for item in items if not item.get("closed")]
        elif args.status == "closed":
            filtered = [item for item in items if item.get("closed")]
        else:
            filtered = items
        return filtered[: args.limit]

    try:
        if not args.markets_only:
            print(f"Fetching events (status={args.status})...")
            events_resp = client.events.list(
                {"limit": fetch_limit, "active": True, **closed_param}
            )
            events = events_resp.get("events", events_resp) if isinstance(events_resp, dict) else events_resp
            if isinstance(events, list):
                if args.verbose and events:
                    print(f"\n  [VERBOSE] First event raw JSON:\n")
                    print(_indent(json.dumps(events[0], indent=2, default=str), "    "))
                    print()
                events = _filter_by_status(events)
                display_events(events, args.limit)
            else:
                print(f"  Events response: {events_resp}")

        if not args.events_only:
            print(f"Fetching markets (status={args.status})...")
            markets_resp = client.markets.list(
                {"limit": fetch_limit, **closed_param}
            )
            markets = markets_resp.get("markets", markets_resp) if isinstance(markets_resp, dict) else markets_resp
            if isinstance(markets, list):
                if args.verbose and markets:
                    print(f"\n  [VERBOSE] First market raw JSON:\n")
                    print(_indent(json.dumps(markets[0], indent=2, default=str), "    "))
                    print()
                markets = _filter_by_status(markets)
                display_markets(markets, args.limit)
            else:
                print(f"  Markets response: {markets_resp}")

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

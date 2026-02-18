"""Async dashboard - fetch multiple resources concurrently.

Requires authentication. Demonstrates:
- AsyncPolymarketUS client with context manager
- asyncio.gather for concurrent API calls
- Combined dashboard view of account state

Usage:
    uv run 06_async_dashboard.py
    uv run 06_async_dashboard.py --include-markets
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from polymarket_us import AsyncPolymarketUS
from polymarket_us import (
    AuthenticationError,
    APIConnectionError,
    APITimeoutError,
)


def print_section(title: str) -> None:
    print(f"\n  {'-' * 50}")
    print(f"  {title}")
    print(f"  {'-' * 50}")


def _amt(value: dict | str | None) -> str:
    """Extract display value from an Amount dict {"value": "0.55", "currency": "USD"}."""
    if isinstance(value, dict):
        return value.get("value", "?")
    return str(value) if value is not None else "?"


def _activity_summary(act: dict) -> str:
    """Extract a one-line summary from a polymorphic activity dict."""
    act_type = act.get("type", "?")
    short_type = act_type.replace("ACTIVITY_TYPE_", "")

    if act_type == "ACTIVITY_TYPE_TRADE" and "trade" in act:
        trade = act["trade"]
        execution = trade.get("aggressorExecution", {})
        order = execution.get("order", {})
        metadata = order.get("marketMetadata", {})
        title = metadata.get("title", order.get("marketSlug", "?"))
        outcome = metadata.get("outcome", "")
        intent = order.get("intent", "?").replace("ORDER_INTENT_", "")
        price = _amt(execution.get("lastPx", order.get("price")))
        label = f"{title}" + (f" ({outcome})" if outcome else "")
        return f"[{short_type}] {label} - {intent} @ ${price}"

    if act_type == "ACTIVITY_TYPE_POSITION_RESOLUTION" and "positionResolution" in act:
        res = act["positionResolution"]
        before = res.get("beforePosition", {})
        metadata = before.get("marketMetadata", {})
        title = metadata.get("title", res.get("marketSlug", "?"))
        outcome = metadata.get("outcome", "")
        net = before.get("netPosition", "?")
        label = f"{title}" + (f" ({outcome})" if outcome else "")
        return f"[{short_type}] {label} - pos={net}"

    # Fallback for unknown types
    payload_key = next((k for k in act if k != "type"), None)
    if payload_key and isinstance(act[payload_key], dict):
        slug = act[payload_key].get("marketSlug", "?")
        return f"[{short_type}] {slug}"
    return f"[{short_type}]"


def display_dashboard(
    balances: dict,
    positions: dict | list,
    activities: dict | list,
    events: dict | list | None = None,
    markets: dict | list | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(f"\n{'=' * 60}")
    print(f"  Polymarket US Dashboard")
    print(f"  Generated: {now}")
    print(f"{'=' * 60}")

    # Balances - response: {"balances": [{"currentBalance": ..., "buyingPower": ..., ...}]}
    print_section("Account Balances")
    bal_list = balances.get("balances", []) if isinstance(balances, dict) else []
    if bal_list:
        bal = bal_list[0]
        print(f"    Balance:............ ${bal.get('currentBalance', 0):,.2f}")
        print(f"    Buying Power:....... ${bal.get('buyingPower', 0):,.2f}")
        print(f"    Asset Notional:..... ${bal.get('assetNotional', 0):,.2f}")
        print(f"    Open Orders:........ ${bal.get('openOrders', 0):,.2f}")
        print(f"    Unsettled Funds:.... ${bal.get('unsettledFunds', 0):,.2f}")
    else:
        print("    No balance data")

    # Positions - response: {"positions": {"slug": {...}, ...}, "eof": bool}
    print_section("Open Positions")
    pos_dict = positions.get("positions", {}) if isinstance(positions, dict) else {}
    if isinstance(pos_dict, dict) and pos_dict:
        for slug, pos in pos_dict.items():
            metadata = pos.get("marketMetadata", {})
            title = metadata.get("title", slug)
            outcome = metadata.get("outcome", "")
            net = pos.get("netPosition", "?")
            cost = _amt(pos.get("cost"))
            label = f"{title}" + (f" ({outcome})" if outcome else "")
            print(f"    {label}  net={net}  cost=${cost}")
    else:
        print("    No open positions")

    # Recent Activity - response: {"activities": [...], "nextCursor": ..., "eof": bool}
    print_section("Recent Activity (last 5)")
    act_list = activities.get("activities", []) if isinstance(activities, dict) else []
    if isinstance(act_list, list) and act_list:
        for act in act_list[:5]:
            print(f"    {_activity_summary(act)}")
    else:
        print("    No recent activity")

    # Events (optional)
    if events is not None:
        print_section("Trending Events")
        ev_list = events.get("events", []) if isinstance(events, dict) else []
        if isinstance(ev_list, list) and ev_list:
            for ev in ev_list[:5]:
                title = ev.get("title", "Untitled")
                category = ev.get("category", "")
                status = "Closed" if ev.get("closed") else "Open"
                num_markets = len(ev.get("markets", []))
                print(f"    {title} | {category} | {status} | {num_markets} market(s)")
        else:
            print("    No events found")

    # Markets (optional)
    if markets is not None:
        print_section("Active Markets")
        mkt_list = markets.get("markets", []) if isinstance(markets, dict) else []
        if isinstance(mkt_list, list) and mkt_list:
            for mkt in mkt_list[:5]:
                question = mkt.get("question", "Untitled")
                slug = mkt.get("slug", "?")
                status = "Closed" if mkt.get("closed") else "Open"
                market_type = mkt.get("marketType", "?")
                print(f"    {question} ({slug}) | {market_type} | {status}")
        else:
            print("    No markets found")

    print(f"\n{'=' * 60}\n")


async def run_dashboard(include_markets: bool) -> None:
    load_dotenv()
    key_id = os.environ.get("POLYMARKET_KEY_ID")
    secret_key = os.environ.get("POLYMARKET_SECRET_KEY")

    if not key_id or not secret_key:
        print("Error: Missing API credentials.", file=sys.stderr)
        print("Set POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY environment variables.", file=sys.stderr)
        sys.exit(1)

    async with AsyncPolymarketUS(key_id=key_id, secret_key=secret_key) as client:
        print("Fetching dashboard data concurrently...")

        # Build tasks
        tasks = [
            client.account.balances(),
            client.portfolio.positions(),
            client.portfolio.activities(),
        ]

        if include_markets:
            tasks.append(client.events.list({"limit": 5, "active": True}))
            tasks.append(client.markets.list({"limit": 5}))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Check for exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"\n  Warning: Task {i} failed - {result}", file=sys.stderr)

        # Unpack results (use empty dict for failed tasks)
        balances = results[0] if not isinstance(results[0], Exception) else {}
        positions = results[1] if not isinstance(results[1], Exception) else {}
        activities = results[2] if not isinstance(results[2], Exception) else {}
        events = None
        markets = None

        if include_markets and len(results) > 3:
            events = results[3] if not isinstance(results[3], Exception) else None
            markets = results[4] if not isinstance(results[4], Exception) else None

        display_dashboard(balances, positions, activities, events, markets)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket US async dashboard")
    parser.add_argument("--include-markets", action="store_true",
                        help="Also fetch trending events and markets")
    args = parser.parse_args()

    try:
        asyncio.run(run_dashboard(args.include_markets))
    except AuthenticationError as e:
        print(f"\nAuthentication failed: {e.message}", file=sys.stderr)
        sys.exit(1)
    except APIConnectionError as e:
        print(f"\nConnection error: {e.message}", file=sys.stderr)
        sys.exit(1)
    except APITimeoutError:
        print("\nRequest timed out.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

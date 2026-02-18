"""View account balances, positions, and recent activity.

Requires authentication. Demonstrates:
- Fetching account balances (buying power, asset values)
- Fetching portfolio positions
- Fetching activity history (trades, resolutions, balance changes)
- Formatted table output

Usage:
    uv run 04_account_portfolio.py
    uv run 04_account_portfolio.py --positions-only
    uv run 04_account_portfolio.py --activities-only
"""

import argparse
import os
import sys

from dotenv import load_dotenv

from polymarket_us import PolymarketUS
from polymarket_us import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
)


def get_authenticated_client() -> PolymarketUS:
    load_dotenv()
    key_id = os.environ.get("POLYMARKET_KEY_ID")
    secret_key = os.environ.get("POLYMARKET_SECRET_KEY")

    if not key_id or not secret_key:
        print("Error: Missing API credentials.", file=sys.stderr)
        print("Set POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY environment variables.", file=sys.stderr)
        print("Generate keys at: https://polymarket.us/developer", file=sys.stderr)
        sys.exit(1)

    return PolymarketUS(key_id=key_id, secret_key=secret_key)


def _amt(value: dict | str | None) -> str:
    """Extract display value from an Amount dict {"value": "0.55", "currency": "USD"}."""
    if isinstance(value, dict):
        return value.get("value", "?")
    return str(value) if value is not None else "?"


def display_balances(balances_resp: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Account Balances")
    print(f"{'=' * 60}\n")

    # Response: {"balances": [{"currentBalance": 7.28, "buyingPower": 7.28, ...}]}
    bal_list = balances_resp.get("balances", []) if isinstance(balances_resp, dict) else []

    if not bal_list:
        print("  No balance data available.")
        print()
        return

    for bal in bal_list:
        currency = bal.get("currency", "USD")
        print(f"  Currency:             {currency}")
        print(f"  Current Balance:      ${bal.get('currentBalance', 0):,.2f}")
        print(f"  Buying Power:         ${bal.get('buyingPower', 0):,.2f}")
        print(f"  Asset Notional:       ${bal.get('assetNotional', 0):,.2f}")
        print(f"  Asset Available:      ${bal.get('assetAvailable', 0):,.2f}")
        print(f"  Pending Credit:       ${bal.get('pendingCredit', 0):,.2f}")
        print(f"  Open Orders:          ${bal.get('openOrders', 0):,.2f}")
        print(f"  Unsettled Funds:      ${bal.get('unsettledFunds', 0):,.2f}")
        print(f"  Margin Requirement:   ${bal.get('marginRequirement', 0):,.2f}")
        withdrawals = bal.get("pendingWithdrawals", [])
        if withdrawals:
            print(f"  Pending Withdrawals:  {len(withdrawals)}")
        print()


def display_positions(positions_resp: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Portfolio Positions")
    print(f"{'=' * 60}\n")

    # Response: {"positions": { "slug": {...}, ... }, "availablePositions": [...], "eof": bool}
    positions = positions_resp.get("positions", {}) if isinstance(positions_resp, dict) else {}
    available = positions_resp.get("availablePositions", []) if isinstance(positions_resp, dict) else []

    # positions is a dict keyed by market slug
    if isinstance(positions, dict) and positions:
        for i, (slug, pos) in enumerate(positions.items(), 1):
            metadata = pos.get("marketMetadata", {})
            title = metadata.get("title", slug)
            outcome = metadata.get("outcome", "N/A")
            net = pos.get("netPosition", "0")
            cost = _amt(pos.get("cost"))
            realized = _amt(pos.get("realized"))
            cash_value = _amt(pos.get("cashValue"))
            qty_bought = pos.get("qtyBought", "0")
            qty_sold = pos.get("qtySold", "0")

            print(f"  {i:>3}. {title} — {outcome}")
            print(f"       Slug: {slug}")
            print(f"       Net Position: {net} | Bought: {qty_bought} | Sold: {qty_sold}")
            print(f"       Cost: ${cost} | Realized: ${realized} | Cash Value: ${cash_value}")
            print()
    elif available:
        print(f"  No active positions, but {len(available)} settled/available position(s).\n")
    else:
        print("  No open positions.\n")


def _format_activity_trade(trade: dict) -> list[str]:
    """Format an ACTIVITY_TYPE_TRADE payload into display lines."""
    execution = trade.get("aggressorExecution", {})
    order = execution.get("order", {})
    metadata = order.get("marketMetadata", {})

    title = metadata.get("title", order.get("marketSlug", "?"))
    outcome = metadata.get("outcome", "")
    slug = order.get("marketSlug", "?")
    intent = order.get("intent", "?")
    price = _amt(execution.get("lastPx", order.get("price")))
    qty = execution.get("lastShares", str(order.get("quantity", "?")))
    exec_type = execution.get("type", "?")
    time = execution.get("transactTime", "?")

    label = f"{title}" + (f" ({outcome})" if outcome else "")
    return [
        f"       {label}",
        f"       Slug: {slug} | {intent}",
        f"       Price: ${price} | Qty: {qty} | Exec: {exec_type}",
        f"       Time: {time}",
    ]


def _format_activity_resolution(resolution: dict) -> list[str]:
    """Format an ACTIVITY_TYPE_POSITION_RESOLUTION payload into display lines."""
    slug = resolution.get("marketSlug", "?")
    before = resolution.get("beforePosition", {})
    after = resolution.get("afterPosition", {})
    metadata = before.get("marketMetadata", after.get("marketMetadata", {}))

    title = metadata.get("title", slug)
    outcome = metadata.get("outcome", "")
    net_before = before.get("netPosition", "?")
    cost = _amt(before.get("cost"))
    realized_after = _amt(after.get("realized")) if after else "?"
    time = after.get("updateTime", before.get("updateTime", "?")) if after else before.get("updateTime", "?")

    label = f"{title}" + (f" ({outcome})" if outcome else "")
    return [
        f"       {label}",
        f"       Slug: {slug}",
        f"       Position: {net_before} | Cost: ${cost} | Realized: ${realized_after}",
        f"       Time: {time}",
    ]


def display_activities(activities_resp: dict | list) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Recent Activity")
    print(f"{'=' * 60}\n")

    # Response: {"activities": [...], "nextCursor": "...", "eof": bool}
    activities = activities_resp
    if isinstance(activities_resp, dict):
        activities = activities_resp.get("activities", [])

    if not isinstance(activities, list) or not activities:
        print("  No recent activity.\n")
        return

    for i, act in enumerate(activities, 1):
        act_type = act.get("type", "UNKNOWN")
        # Pretty-print the type
        short_type = act_type.replace("ACTIVITY_TYPE_", "")

        print(f"  {i:>3}. [{short_type}]")

        if act_type == "ACTIVITY_TYPE_TRADE" and "trade" in act:
            lines = _format_activity_trade(act["trade"])
        elif act_type == "ACTIVITY_TYPE_POSITION_RESOLUTION" and "positionResolution" in act:
            lines = _format_activity_resolution(act["positionResolution"])
        else:
            # Fallback: try to extract any nested payload
            payload_key = next((k for k in act if k != "type"), None)
            if payload_key:
                payload = act[payload_key]
                slug = payload.get("marketSlug", "?") if isinstance(payload, dict) else "?"
                lines = [f"       Market: {slug}"]
            else:
                lines = [f"       (no details)"]

        for line in lines:
            print(line)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="View Polymarket US account and portfolio")
    parser.add_argument("--positions-only", action="store_true", help="Only show positions")
    parser.add_argument("--activities-only", action="store_true", help="Only show activities")
    args = parser.parse_args()

    client = get_authenticated_client()

    try:
        show_all = not args.positions_only and not args.activities_only

        if show_all:
            print("Fetching account balances...")
            balances = client.account.balances()
            display_balances(balances)

        if show_all or args.positions_only:
            print("Fetching portfolio positions...")
            positions = client.portfolio.positions()
            display_positions(positions)

        if show_all or args.activities_only:
            print("Fetching recent activity...")
            activities = client.portfolio.activities()
            display_activities(activities)

    except AuthenticationError as e:
        print(f"\nAuthentication failed: {e.message}", file=sys.stderr)
        print("Check your POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY.", file=sys.stderr)
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

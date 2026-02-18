"""View the order book and best bid/offer for a Polymarket US market.

No authentication required. Demonstrates:
- Fetching the full order book (book)
- Fetching the top-of-book BBO (best bid/offer)
- Market settlement info
- Formatted depth display

Usage:
    uv run 03_orderbook_viewer.py btc-100k-2025
    uv run 03_orderbook_viewer.py "presidential-election-2028" --depth 10
    uv run 03_orderbook_viewer.py some-market-slug --bbo-only
"""

import argparse
import json
import sys

from polymarket_us import PolymarketUS
from polymarket_us import (
    APIConnectionError,
    APITimeoutError,
    NotFoundError,
)


def _amt(value: dict | str | None) -> str | None:
    """Extract display value from an Amount dict {"value": "0.55", "currency": "USD"}."""
    if isinstance(value, dict):
        return value.get("value")
    return str(value) if value is not None else None


def display_bbo(bbo_resp: dict, slug: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Best Bid/Offer — {slug}")
    print(f"{'=' * 60}\n")

    # BBO response nests data under "marketData"
    md = bbo_resp.get("marketData", bbo_resp) if isinstance(bbo_resp, dict) else {}

    bid = _amt(md.get("bestBid"))
    ask = _amt(md.get("bestAsk"))
    current_px = _amt(md.get("currentPx"))
    last_trade = _amt(md.get("lastTradePx"))
    settlement = _amt(md.get("settlementPx"))
    bid_depth = md.get("bidDepth", "N/A")
    ask_depth = md.get("askDepth", "N/A")

    print(f"  Best Bid:      {bid or '(empty)'}")
    print(f"  Best Ask:      {ask or '(empty)'}")

    # Calculate spread if both sides exist
    if bid and ask:
        try:
            spread = float(ask) - float(bid)
            print(f"  Spread:        {spread:.4f}")
        except (ValueError, TypeError):
            pass

    print(f"  Bid Depth:     {bid_depth}")
    print(f"  Ask Depth:     {ask_depth}")
    print(f"  Current Px:    {current_px or 'N/A'}")
    print(f"  Last Trade Px: {last_trade or 'N/A'}")
    print(f"  Settlement Px: {settlement or 'N/A'}")
    print()


def display_book(book_resp: dict, slug: str, depth: int) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Order Book — {slug}")
    print(f"{'=' * 60}\n")

    # Book response nests data under "marketData"
    md = book_resp.get("marketData", book_resp) if isinstance(book_resp, dict) else {}

    bids = md.get("bids", [])
    offers = md.get("offers", [])
    state = md.get("state", "N/A")

    print(f"  Market State: {state}\n")

    # Display asks/offers (reversed so best ask is at bottom, near spread)
    print(f"  {'ASKS / OFFERS':^50}")
    print(f"  {'Price':>15}  {'Size':>15}")
    print(f"  {'-' * 35}")

    display_offers = offers[:depth] if offers else []
    for offer in reversed(display_offers):
        # Each level: {"px": {"value": "0.55", "currency": "USD"}, "qty": "10"}
        price = _amt(offer.get("px")) or "?"
        qty = offer.get("qty", "?")
        print(f"  {price:>15}  {qty:>15}")

    print(f"  {'--- SPREAD ---':^35}")

    # Display bids
    print(f"  {'BIDS':^50}")
    print(f"  {'Price':>15}  {'Size':>15}")
    print(f"  {'-' * 35}")

    display_bids = bids[:depth] if bids else []
    for bid in display_bids:
        price = _amt(bid.get("px")) or "?"
        qty = bid.get("qty", "?")
        print(f"  {price:>15}  {qty:>15}")

    if not bids and not offers:
        print("  (empty order book)")

    # Print stats
    stats = md.get("stats", {})
    if stats:
        print(f"\n  Book Stats:")
        close_px = _amt(stats.get("closePx"))
        settlement_px = _amt(stats.get("settlementPx"))
        current_px = _amt(stats.get("currentPx"))
        if close_px:
            print(f"    Close Px:      ${close_px}")
        if settlement_px:
            print(f"    Settlement Px: ${settlement_px}")
        if current_px:
            print(f"    Current Px:    ${current_px}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="View order book for a Polymarket US market")
    parser.add_argument("slug", type=str, help="Market slug (e.g. btc-100k-2025)")
    parser.add_argument("--depth", type=int, default=5, help="Number of price levels to show (default: 5)")
    parser.add_argument("--bbo-only", action="store_true", help="Only show best bid/offer")
    parser.add_argument("--settlement", action="store_true", help="Also show settlement info")
    args = parser.parse_args()

    client = PolymarketUS()

    try:
        # Always fetch BBO
        print(f"Fetching BBO for '{args.slug}'...")
        bbo = client.markets.bbo(args.slug)
        display_bbo(bbo, args.slug)

        if not args.bbo_only:
            print(f"Fetching full order book for '{args.slug}'...")
            book = client.markets.book(args.slug)
            display_book(book, args.slug, args.depth)

        if args.settlement:
            print(f"Fetching settlement info for '{args.slug}'...")
            try:
                settlement = client.markets.settlement(args.slug)
                print(f"\n  Settlement Info:")
                if isinstance(settlement, dict):
                    for k, v in settlement.items():
                        print(f"    {k}: {v}")
                else:
                    print(f"    {settlement}")
            except NotFoundError:
                print("  Settlement data not available (market may not be settled yet).")
            print()

    except NotFoundError as e:
        print(f"\nMarket not found: '{args.slug}' — {e.message}", file=sys.stderr)
        print("Tip: Use 02_search_markets.py to find valid market slugs.", file=sys.stderr)
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

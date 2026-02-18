"""Preview and place limit orders on Polymarket US.

Requires authentication. Demonstrates:
- Order preview (dry-run validation)
- Limit order creation with confirmation
- Order intent types (BUY_LONG, BUY_SHORT)
- Time-in-force options
- Order cancellation

Usage:
    uv run 05_place_order.py --market btc-100k-2025 --side long --price 0.55 --qty 10
    uv run 05_place_order.py --market btc-100k-2025 --side short --price 0.45 --qty 5
    uv run 05_place_order.py --market btc-100k-2025 --side long --price 0.55 --qty 10 --preview-only
    uv run 05_place_order.py --list-open
    uv run 05_place_order.py --cancel-all
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from polymarket_us import PolymarketUS
from polymarket_us import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    APIConnectionError,
    APITimeoutError,
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


def display_order_result(result: dict, label: str = "Order") -> None:
    print(f"\n  {label} Result:")
    if isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, dict):
                print(f"    {k}:")
                for k2, v2 in v.items():
                    print(f"      {k2}: {v2}")
            else:
                print(f"    {k}: {v}")
    else:
        print(f"    {result}")
    print()


def list_open_orders(client: PolymarketUS) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Open Orders")
    print(f"{'=' * 60}\n")

    orders_resp = client.orders.list()
    orders = orders_resp
    if isinstance(orders_resp, dict):
        orders = orders_resp.get("orders", orders_resp)

    if isinstance(orders, list):
        if not orders:
            print("  No open orders.\n")
            return
        for i, order in enumerate(orders, 1):
            market = order.get("marketSlug", order.get("market", "N/A"))
            side = order.get("intent", order.get("side", "N/A"))
            price = order.get("price", "N/A")
            qty = order.get("quantity", order.get("remainingQuantity", "N/A"))
            order_id = order.get("id", order.get("exchangeId", "N/A"))
            print(f"  {i:>3}. [{side}] {market}")
            print(f"       Price: {price} | Qty: {qty} | ID: {order_id}")
            print()
    else:
        print(f"  {orders_resp}")


def build_order_params(args: argparse.Namespace) -> dict:
    intent = "ORDER_INTENT_BUY_LONG" if args.side == "long" else "ORDER_INTENT_BUY_SHORT"

    tif_map = {
        "gtc": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "ioc": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
        "fok": "TIME_IN_FORCE_FILL_OR_KILL",
    }
    tif = tif_map.get(args.tif, "TIME_IN_FORCE_GOOD_TILL_CANCEL")

    return {
        "marketSlug": args.market,
        "intent": intent,
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(args.price), "currency": "USD"},
        "quantity": args.qty,
        "tif": tif,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview and place orders on Polymarket US")

    # Order params
    parser.add_argument("--market", type=str, help="Market slug (e.g. btc-100k-2025)")
    parser.add_argument("--side", choices=["long", "short"], help="Order side: long or short")
    parser.add_argument("--price", type=float, help="Limit price (0.01 - 0.99)")
    parser.add_argument("--qty", type=int, help="Quantity (whole contracts)")
    parser.add_argument("--tif", choices=["gtc", "ioc", "fok"], default="gtc",
                        help="Time-in-force: gtc (default), ioc, fok")

    # Actions
    parser.add_argument("--preview-only", action="store_true", help="Only preview, do not submit")
    parser.add_argument("--list-open", action="store_true", help="List open orders and exit")
    parser.add_argument("--cancel-all", action="store_true", help="Cancel all open orders")
    args = parser.parse_args()

    client = get_authenticated_client()

    try:
        if args.list_open:
            list_open_orders(client)
            return

        if args.cancel_all:
            print("Cancelling all open orders...")
            result = client.orders.cancel_all()
            print(f"  Result: {result}")
            return

        # Validate required params for order placement
        if not all([args.market, args.side, args.price, args.qty]):
            parser.error("--market, --side, --price, and --qty are required for placing orders")

        order_params = build_order_params(args)

        # Preview
        print(f"\n{'=' * 60}")
        print(f"  Order Preview")
        print(f"{'=' * 60}\n")
        print(f"  Market:    {args.market}")
        print(f"  Side:      {args.side.upper()}")
        print(f"  Price:     ${args.price}")
        print(f"  Quantity:  {args.qty} contracts")
        print(f"  TIF:       {args.tif.upper()}")
        print(f"  Cost:      ~${args.price * args.qty:.2f}")
        print()

        try:
            print("  Running order preview (dry-run)...")
            preview = client.orders.preview(order_params)
            display_order_result(preview, "Preview")
        except BadRequestError as e:
            print(f"\n  Preview failed: {e.message}", file=sys.stderr)
            print("  The order parameters may be invalid.", file=sys.stderr)
            sys.exit(1)

        if args.preview_only:
            print("  Preview only mode — order NOT submitted.")
            return

        # Confirmation prompt
        confirm = input("  Submit this order? (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("  Order cancelled by user.")
            return

        # Place the order
        print("\n  Submitting order...")
        result = client.orders.create(order_params)
        display_order_result(result, "Order Created")

    except AuthenticationError as e:
        print(f"\nAuthentication failed: {e.message}", file=sys.stderr)
        sys.exit(1)
    except NotFoundError as e:
        print(f"\nMarket not found: {e.message}", file=sys.stderr)
        sys.exit(1)
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

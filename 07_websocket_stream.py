"""Stream real-time market data and private updates via WebSocket.

Requires authentication. Demonstrates:
- WebSocket market data streaming (order book, trades)
- WebSocket private channel (orders, positions, balances)
- Event-driven handlers with on() callbacks
- Graceful shutdown with Ctrl+C

Usage:
    uv run 07_websocket_stream.py --market btc-100k-2025
    uv run 07_websocket_stream.py --market btc-100k-2025 --market eth-5k-2025
    uv run 07_websocket_stream.py --private
    uv run 07_websocket_stream.py --market btc-100k-2025 --private
"""

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from polymarket_us import AsyncPolymarketUS
from polymarket_us import (
    AuthenticationError,
    APIConnectionError,
)


shutdown_event = asyncio.Event()


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# ── Helpers ───────────────────────────────────────────────────

def _amt(value: dict | str | None) -> str:
    """Extract display value from an Amount dict {"value": "0.55", "currency": "USD"}."""
    if isinstance(value, dict):
        return value.get("value", "?")
    return str(value) if value is not None else "?"


# ── Market data handlers ──────────────────────────────────────

def on_market_data(data: dict) -> None:
    md = data.get("marketData", {})
    slug = md.get("marketSlug", "?")

    # Best bid = top of bids list, best ask = top of offers list
    bids = md.get("bids", [])
    offers = md.get("offers", [])
    best_bid = _amt(bids[0].get("px")) if bids else "-"
    best_ask = _amt(offers[0].get("px")) if offers else "-"
    bid_depth = len(bids)
    ask_depth = len(offers)

    stats = md.get("stats", {})
    last_trade = _amt(stats.get("lastTradePx"))

    print(f"  [{timestamp()}] MARKET_DATA  {slug}  bid={best_bid} ({bid_depth}lvl)  ask={best_ask} ({ask_depth}lvl)  last={last_trade}")


def on_market_data_lite(data: dict) -> None:
    md = data.get("marketDataLite", {})
    slug = md.get("marketSlug", "?")
    bid = _amt(md.get("bestBid"))
    ask = _amt(md.get("bestAsk"))
    last = _amt(md.get("lastTradePx"))
    print(f"  [{timestamp()}] MARKET_LITE  {slug}  bid={bid}  ask={ask}  last={last}")


def on_trade(data: dict) -> None:
    trade = data.get("trade", {})
    slug = trade.get("marketSlug", "?")
    price = _amt(trade.get("price"))
    qty = _amt(trade.get("quantity"))
    taker = trade.get("taker", {})
    taker_side = taker.get("side", "?")
    trade_time = trade.get("tradeTime", "")
    print(f"  [{timestamp()}] TRADE        {slug}  price={price}  qty={qty}  taker={taker_side}  at={trade_time}")


# ── Private channel handlers ──────────────────────────────────

def on_order_snapshot(data: dict) -> None:
    snapshot = data.get("orderSubscriptionSnapshot", data.get("ordersSnapshot", {}))
    orders = snapshot.get("orders", [])
    print(f"  [{timestamp()}] ORDER_SNAP   {len(orders)} open order(s)")
    for o in orders[:5]:
        market = o.get("marketSlug", "?")
        intent = o.get("intent", "?")
        price = _amt(o.get("price"))
        state = o.get("state", "?")
        leaves = o.get("leavesQuantity", "?")
        print(f"                    {market} [{intent}] @ ${price}  qty_left={leaves}  state={state}")


def on_order_update(data: dict) -> None:
    update = data.get("orderSubscriptionUpdate", data.get("orderUpdate", {}))
    execution = update.get("execution", {})
    exec_type = execution.get("type", "?")
    order = execution.get("order", {})
    market = order.get("marketSlug", "?")
    intent = order.get("intent", "?")
    state = order.get("state", "?")
    last_px = _amt(execution.get("lastPx"))
    print(f"  [{timestamp()}] ORDER_UPDATE {market} [{intent}] exec={exec_type}  lastPx={last_px}  state={state}")


def on_position_snapshot(data: dict) -> None:
    snapshot = data.get("positionSubscriptionSnapshot", data.get("positionsSnapshot", {}))
    positions = snapshot.get("positions", {})  # dict keyed by market slug
    print(f"  [{timestamp()}] POS_SNAP     {len(positions)} position(s)")
    for slug, pos in list(positions.items())[:5]:
        net = pos.get("netPosition", "?")
        cash = _amt(pos.get("cashValue"))
        print(f"                    {slug}  net={net}  cashValue=${cash}")


def on_position_update(data: dict) -> None:
    update = data.get("positionSubscriptionUpdate", data.get("positionUpdate", {}))
    slug = update.get("marketSlug", "?")
    pos = update.get("position", {})
    net = pos.get("netPosition", "?")
    cash = _amt(pos.get("cashValue"))
    print(f"  [{timestamp()}] POS_UPDATE   {slug}  net={net}  cashValue=${cash}")


def on_balance_snapshot(data: dict) -> None:
    snapshot = data.get("accountBalanceSubscriptionSnapshot", data.get("accountBalancesSnapshot", {}))
    balance = snapshot.get("balance", "?")
    buying_power = snapshot.get("buyingPower", "?")
    print(f"  [{timestamp()}] BAL_SNAP     balance=${balance}  buyingPower=${buying_power}")


def on_balance_update(data: dict) -> None:
    update = data.get("accountBalanceSubscriptionUpdate", data.get("accountBalanceUpdate", {}))
    balance = update.get("balance", "?")
    buying_power = update.get("buyingPower", "?")
    print(f"  [{timestamp()}] BAL_UPDATE   balance=${balance}  buyingPower=${buying_power}")


def on_heartbeat(data: dict) -> None:
    # Silent by default; uncomment to see heartbeats
    # print(f"  [{timestamp()}] HEARTBEAT")
    pass


def on_error(data: dict) -> None:
    print(f"  [{timestamp()}] WS_ERROR     {data}", file=sys.stderr)


def on_close(data: dict) -> None:
    print(f"  [{timestamp()}] WS_CLOSE     Connection closed: {data}")


async def stream(market_slugs: list[str], include_private: bool) -> None:
    load_dotenv()
    key_id = os.environ.get("POLYMARKET_KEY_ID")
    secret_key = os.environ.get("POLYMARKET_SECRET_KEY")

    if not key_id or not secret_key:
        print("Error: Missing API credentials.", file=sys.stderr)
        print("Set POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY environment variables.", file=sys.stderr)
        sys.exit(1)

    async with AsyncPolymarketUS(key_id=key_id, secret_key=secret_key) as client:
        connections = []

        # Market data WebSocket
        if market_slugs:
            print(f"\n  Subscribing to market data for: {', '.join(market_slugs)}")

            markets_ws = client.ws.markets()
            markets_ws.on("market_data", on_market_data)
            markets_ws.on("market_data_lite", on_market_data_lite)
            markets_ws.on("trade", on_trade)
            markets_ws.on("heartbeat", on_heartbeat)
            markets_ws.on("error", on_error)
            markets_ws.on("close", on_close)

            await markets_ws.connect()
            connections.append(markets_ws)

            # Subscribe to each market
            for i, slug in enumerate(market_slugs):
                sub_id = f"market-sub-{i}"
                await markets_ws.subscribe(sub_id, "SUBSCRIPTION_TYPE_MARKET_DATA", [slug])
                print(f"  Subscribed: {slug} (id: {sub_id})")

        # Private WebSocket
        if include_private:
            print(f"\n  Subscribing to private channel (orders, positions, balances)")

            private_ws = client.ws.private()
            private_ws.on("order_snapshot", on_order_snapshot)
            private_ws.on("order_update", on_order_update)
            private_ws.on("position_snapshot", on_position_snapshot)
            private_ws.on("position_update", on_position_update)
            private_ws.on("account_balance_snapshot", on_balance_snapshot)
            private_ws.on("account_balance_update", on_balance_update)
            private_ws.on("heartbeat", on_heartbeat)
            private_ws.on("error", on_error)
            private_ws.on("close", on_close)

            await private_ws.connect()
            connections.append(private_ws)

            await private_ws.subscribe("order-sub", "SUBSCRIPTION_TYPE_ORDER")
            await private_ws.subscribe("position-sub", "SUBSCRIPTION_TYPE_POSITION")
            print("  Subscribed to orders and positions")

        print(f"\n{'=' * 60}")
        print(f"  Streaming live data... Press Ctrl+C to stop")
        print(f"{'=' * 60}\n")

        # Wait until shutdown signal
        await shutdown_event.wait()

        print("\n  Shutting down WebSocket connections...")


def handle_signal() -> None:
    shutdown_event.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream real-time Polymarket US data via WebSocket")
    parser.add_argument("--market", type=str, action="append", default=[],
                        help="Market slug(s) to stream (can specify multiple)")
    parser.add_argument("--private", action="store_true",
                        help="Subscribe to private channel (orders, positions, balances)")
    args = parser.parse_args()

    if not args.market and not args.private:
        parser.error("Specify at least one --market or --private")

    loop = asyncio.new_event_loop()

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler; fall back to signal.signal
            signal.signal(sig, lambda s, f: handle_signal())

    try:
        loop.run_until_complete(stream(args.market, args.private))
    except AuthenticationError as e:
        print(f"\nAuthentication failed: {e.message}", file=sys.stderr)
        sys.exit(1)
    except APIConnectionError as e:
        print(f"\nConnection error: {e.message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()

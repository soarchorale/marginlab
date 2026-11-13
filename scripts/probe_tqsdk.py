"""Read-only index/futures data probe; prompts locally for Kuaiqi credentials.

Run with the project interpreter. No Flask import, database writes, or orders.
Tick snapshots are not guaranteed to be individual exchange trades.
"""

import argparse
import asyncio
from contextlib import contextmanager
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import time


PAIRS = {
    "50": ("SSE.000016", "KQ.m@CFFEX.IH"),
    "300": ("SSE.000300", "KQ.m@CFFEX.IF"),
    "500": ("SSE.000905", "KQ.m@CFFEX.IC"),
    "1000": ("SSE.000852", "KQ.m@CFFEX.IM"),
}
FIELDS = (
    "datetime", "instrument_name", "ins_class", "underlying_symbol",
    "last_price", "volume", "amount", "open_interest",
    "bid_price1", "bid_volume1", "ask_price1", "ask_volume1",
)


def snapshot(quote):
    result = {}
    for key in FIELDS:
        value = getattr(quote, key, None)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        result[key] = value
    return result


@contextmanager
def market_api(username, password):
    from tqsdk import TqApi, TqAuth, TqSim

    # TqApi can raise during construction, before its context manager is entered.
    # Own the loop so early authentication/network failures leave no pending tasks.
    loop = asyncio.new_event_loop()
    try:
        with TqApi(TqSim(), auth=TqAuth(username, password), debug=False,
                   disable_print=True, loop=loop) as api:
            yield api
    finally:
        if not loop.is_closed():
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()


def run(args, username, password):
    index, continuous = PAIRS[args.index]
    report = {"index": index, "continuous": continuous, "observations": {}}
    with market_api(username, password) as api:
        index_quote = api.get_quote(index)
        main_quote = api.get_quote(continuous)
        deadline = time.time() + args.seconds
        while not main_quote.underlying_symbol and time.time() < deadline:
            api.wait_update(deadline=deadline)
        actual = main_quote.underlying_symbol
        report["resolved_futures_contract"] = actual or None
        quotes = {index: index_quote}
        if actual:
            quotes[actual] = api.get_quote(actual)
        serials = {}
        for symbol in quotes:
            serials[symbol] = {}
            for kind in ("kline", "tick"):
                try:
                    serials[symbol][kind] = (
                        api.get_kline_serial(symbol, 60, data_length=20)
                        if kind == "kline" else api.get_tick_serial(symbol, data_length=20)
                    )
                except Exception as exc:
                    # Avoid printing exception payloads that could contain credentials.
                    report.setdefault("subscription_errors", []).append(
                        {"symbol": symbol, "kind": kind, "error_type": type(exc).__name__}
                    )
        initial = {symbol: snapshot(q) for symbol, q in quotes.items()}
        changes = {symbol: 0 for symbol in quotes}
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            if api.wait_update(deadline=deadline):
                for symbol, quote in quotes.items():
                    if api.is_changing(quote, ["datetime", "volume", "last_price"]):
                        changes[symbol] += 1
        for symbol, quote in quotes.items():
            item = {"initial": initial[symbol], "final": snapshot(quote),
                    "observed_updates": changes[symbol], "series": {}}
            for kind, frame in serials[symbol].items():
                valid = frame.loc[frame["datetime"] > 0].copy()
                columns = [c for c in (
                    "datetime", "open", "high", "low", "close", "last_price",
                    "volume", "amount", "open_interest", "bid_price1", "ask_price1",
                ) if c in valid.columns]
                item["series"][kind] = {
                    "valid_rows": len(valid),
                    "tail": json.loads(valid[columns].tail(5).to_json(orient="records")),
                }
            report["observations"][symbol] = item
    report["notes"] = [
        "Quote timestamps are exchange-local strings; series timestamps are Unix nanoseconds.",
        "The final K-line may be incomplete. Zero updates outside trading hours are inconclusive.",
        "Nonzero index volume does not establish tradable volume at an index price.",
        "This probe does not verify option-chain coverage or reconstruct VIX.",
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", choices=PAIRS, default="300")
    parser.add_argument("--auth-file", type=Path,
                        help="Local JSON with username/password; values are never printed")
    parser.add_argument("--seconds", type=int, default=30,
                        help="Observation window after subscriptions (not a total connection timeout)")
    args = parser.parse_args()
    if not 5 <= args.seconds <= 300:
        parser.error("--seconds must be between 5 and 300")
    username = os.environ.get("TQ_USERNAME")
    password = os.environ.get("TQ_PASSWORD")
    if args.auth_file:
        try:
            credentials = json.loads(args.auth_file.read_text())
            username, password = credentials["username"], credentials["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("Credentials must be strings")
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("Cannot load auth file: expected JSON with username/password strings")
    if not username or not password:
        if not sys.stdin.isatty():
            parser.error("Run interactively or supply TQ_USERNAME and TQ_PASSWORD locally")
        username = username or input("Kuaiqi username: ").strip()
        password = password or getpass.getpass("Kuaiqi password (hidden): ")
    if not username or not password:
        parser.error("Credentials must not be empty")
    try:
        run(args, username, password)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        message = str(exc).replace(password, "[REDACTED]").replace(username, "[REDACTED]")
        message = re.sub(r"eyJ[A-Za-z0-9_.-]+", "[REDACTED TOKEN]", message)
        print(f"Probe failed ({type(exc).__name__}): {message}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

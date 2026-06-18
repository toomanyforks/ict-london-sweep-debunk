"""Fetch 1-minute EURUSD (or any FX) bars from IBKR via ib_insync, paging backward.

The IBKR gateway must be running (paper gateway on 127.0.0.1:4002) and NO other
session logged into the same IBKR account, or historical requests fail with
Error 162 ("session from a different IP").

FX has no real volume, so we pull MIDPOINT bars (bid/ask midpoint) with 24h data
(useRTH=False). Output parquet matches the Binance fetcher's schema: a tz-aware
UTC index named `ts` with open/high/low/close[/volume] columns, so backtest.py
runs on it unchanged.

Usage:
    python3 fetch_ibkr.py EURUSD --days 180
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
from ib_insync import IB, Forex, util

DATA = Path(__file__).resolve().parent / "data"
HOST, PORT, CLIENT_ID = "127.0.0.1", 4002, 91

CHUNK = "5 D"        # duration per request (1-min bars allow up to ~a week)
SLEEP = 5.0          # seconds between requests — stays under IBKR pacing limits


def fetch(symbol: str, days: int) -> Path:
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=20)
    print(f"connected (server {ib.client.serverVersion()})")
    contract = Forex(symbol)
    ib.qualifyContracts(contract)

    target_start = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=days)
    frames = []
    end = ""  # '' = now
    seen_earliest = None
    while True:
        bars = ib.reqHistoricalData(
            contract, endDateTime=end, durationStr=CHUNK,
            barSizeSetting="1 min", whatToShow="MIDPOINT",
            useRTH=False, formatDate=2,  # epoch -> UTC
        )
        if not bars:
            print("  empty response — reached end of available history")
            break
        df = util.df(bars)
        earliest = pd.to_datetime(df["date"].iloc[0], utc=True)
        latest = pd.to_datetime(df["date"].iloc[-1], utc=True)
        frames.append(df)
        print(f"  {len(df):5d} bars  {earliest} -> {latest}")
        # stop when we've reached the target window or stopped making progress
        if earliest.tz_localize(None) <= target_start:
            break
        if seen_earliest is not None and earliest >= seen_earliest:
            print("  no further history advancing — stopping")
            break
        seen_earliest = earliest
        end = earliest.strftime("%Y%m%d-%H:%M:%S")  # UTC; gateway accepts this
        time.sleep(SLEEP)

    ib.disconnect()
    if not frames:
        raise RuntimeError("no data fetched")

    all_df = pd.concat(frames, ignore_index=True)
    all_df["ts"] = pd.to_datetime(all_df["date"], utc=True)
    keep = ["ts", "open", "high", "low", "close"]
    if "volume" in all_df.columns:
        keep.append("volume")
    out = (all_df[keep].drop_duplicates("ts").sort_values("ts").set_index("ts"))
    out = out[out.index >= target_start.tz_localize("UTC")]
    path = DATA / f"{symbol}_1m.parquet"
    out.to_parquet(path)
    print(f"\n{symbol}: {len(out)} bars  {out.index[0]} -> {out.index[-1]}  -> {path}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="EURUSD")
    ap.add_argument("--days", type=int, default=180)
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        fetch(args.symbol, args.days)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

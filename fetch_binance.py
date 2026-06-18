"""Fetch 1-minute spot klines from Binance's public data archive.

Binance's live REST API is geo-blocked in the US, but the historical flat-file
archive at data.binance.vision is open and requires no auth. We download the
monthly zip per symbol, concat, and cache to parquet.

Usage:
    python3 fetch_binance.py BTCUSDT ETHUSDT --start 2024-01 --end 2025-06
"""
import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
BASE = "https://data.binance.vision/data/spot/monthly/klines"

# Binance kline CSV columns (no header in the file).
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore",
]


def months(start: str, end: str):
    s = pd.Period(start, "M")
    e = pd.Period(end, "M")
    p = s
    while p <= e:
        yield f"{p.year:04d}-{p.month:02d}"
        p += 1


def fetch_month(symbol: str, ym: str) -> pd.DataFrame | None:
    url = f"{BASE}/{symbol}/1m/{symbol}-1m-{ym}.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            blob = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"  {symbol} {ym}: skip ({e})", file=sys.stderr)
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            df = pd.read_csv(f, header=None)
    # Newer archives ship a header row and/or microsecond timestamps.
    if str(df.iloc[0, 0]).startswith("open_time"):
        df = df.iloc[1:].reset_index(drop=True)
    df.columns = COLS[: df.shape[1]]
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    ot = pd.to_numeric(df["open_time"], errors="coerce")
    # ms vs microseconds: ms ~1e12, micros ~1e15 for 2020s dates.
    unit = "us" if ot.iloc[0] > 1e14 else "ms"
    df["ts"] = pd.to_datetime(ot, unit=unit, utc=True)
    return df.drop(columns=["open_time"]).dropna(subset=["ts", "close"])


def fetch(symbol: str, start: str, end: str) -> Path:
    parts = []
    for ym in months(start, end):
        d = fetch_month(symbol, ym)
        if d is not None:
            parts.append(d)
            print(f"  {symbol} {ym}: {len(d)} bars")
    if not parts:
        raise RuntimeError(f"no data fetched for {symbol}")
    df = pd.concat(parts, ignore_index=True).drop_duplicates("ts").sort_values("ts")
    df = df.set_index("ts")
    out = DATA / f"{symbol}_1m.parquet"
    df.to_parquet(out)
    print(f"{symbol}: {len(df)} bars  {df.index[0]} -> {df.index[-1]}  -> {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM")
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols:
        fetch(sym, args.start, args.end)


if __name__ == "__main__":
    main()

"""Fetch historical FX 1-minute bars from Dukascopy's free tick datafeed.

Dukascopy serves one LZMA-compressed .bi5 file per hour at
  https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM-1:02d}/{DD:02d}/{HH:02d}h_ticks.bi5
NOTE: the month is ZERO-indexed (January = 00). Each .bi5 decompresses to a
sequence of 20-byte big-endian records: (ms_since_hour:int32, ask:int32,
bid:int32, ask_vol:float32, bid_vol:float32). Prices are integers in points;
divide by 10**digits (EURUSD digits=5). FX is closed Fri 22:00 -> Sun 22:00 UTC,
so weekend hours simply return no ticks.

We aggregate ticks to 1-minute OHLC on the bid/ask MIDPOINT (matching the IBKR
MIDPOINT bars) per day, discarding raw ticks to keep memory flat, then cache to a
parquet with the same schema backtest.py expects (UTC `ts` index + OHLC).

Usage:
    python3 fetch_dukascopy.py EURUSD --start 2024-06-01 --end 2025-06-01
"""
import argparse
import lzma
import struct
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
BASE = "https://datafeed.dukascopy.com/datafeed"
REC = struct.Struct(">iiiff")  # 20 bytes/tick
HEADERS = {"User-Agent": "Mozilla/5.0 (research; 1-min FX backtest)"}
DIGITS = {"EURUSD": 5, "GBPUSD": 5, "AUDUSD": 5, "USDJPY": 3, "USDCHF": 5}


def download(url: str, retries: int = 3) -> bytes | None:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # no data for that hour (weekend/holiday)
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                return None
    return None


def decompress(blob: bytes) -> bytes:
    if not blob:
        return b""
    try:
        return lzma.decompress(blob)  # FORMAT_AUTO handles .lzma (alone) headers
    except lzma.LZMAError:
        return lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(blob)


def fetch_day(symbol: str, day: pd.Timestamp, digits: int):
    div = 10 ** digits
    rows = []
    for h in range(24):
        url = f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{h:02d}h_ticks.bi5"
        raw = download(url)
        if not raw:
            continue
        data = decompress(raw)
        base_ms = int(pd.Timestamp(day.year, day.month, day.day, h, tz="UTC").timestamp() * 1000)
        for off in range(0, len(data) - 19, 20):
            ms, ask, bid, _av, _bv = REC.unpack_from(data, off)
            rows.append((base_ms + ms, (ask + bid) / 2.0 / div))
    if not rows:
        return None
    d = pd.DataFrame(rows, columns=["ms", "mid"])
    d["ts"] = pd.to_datetime(d["ms"], unit="ms", utc=True)
    d = d.set_index("ts").sort_index()
    bars = d["mid"].resample("1min").ohlc().dropna()
    bars["volume"] = d["mid"].resample("1min").count()
    return bars


def fetch(symbol: str, start: str, end: str, workers: int) -> Path:
    digits = DIGITS.get(symbol, 5)
    days = pd.date_range(start, end, freq="D", inclusive="left")
    out_frames = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_day, symbol, d, digits): d for d in days}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                bars = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"  {d.date()}: error {e}", file=sys.stderr)
                bars = None
            if bars is not None and len(bars):
                out_frames[d] = bars
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(days)} days  ({len(out_frames)} with data)")
    if not out_frames:
        raise RuntimeError("no data fetched")
    df = pd.concat([out_frames[k] for k in sorted(out_frames)]).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    path = DATA / f"{symbol}_1m.parquet"
    df.to_parquet(path)
    print(f"\n{symbol}: {len(df)} 1-min bars  {df.index[0]} -> {df.index[-1]}  -> {path}")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="EURUSD")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    fetch(args.symbol, args.start, args.end, args.workers)


if __name__ == "__main__":
    main()

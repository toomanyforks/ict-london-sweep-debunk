"""Backtest the 'london.mp4' ICT session-sweep model on 1-minute crypto data.

Strategy (from the video transcript):
  - At the NY open, mark the Asia-session and London-session high/low.
  - Wait for one of those levels to be SWEPT (price wicks through it and rejects
    back) -> a liquidity grab / stop hunt.
  - Drop to the 1-minute chart; enter on the FAIR VALUE GAP that forms as price
    reverses to the other side. Sweep of a high -> short; sweep of a low -> long.
  - Stop at the sweep extreme.
  - Target: fixed R multiple (poster) OR the next draw-on-liquidity (commenter).

Sessions are wall-clock in America/New_York (DST handled); crypto bars are UTC.
Crypto trades 24/7, so 'session high/low' = the range traced during that clock
window the same way an FX trader would mark it.

Usage:
    python3 backtest.py data/BTCUSDT_1m.parquet --exit both
"""
import argparse
from dataclasses import dataclass, asdict
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")

# ---- session windows, ET wall-clock (start_hour, end_hour), end exclusive ----
ASIA = (19, 0)        # 19:00 prior day -> 00:00  (Tokyo)
LONDON = (2, 8)       # 02:00 -> 08:00            (London, pre-NY)
NY_TRADE = (8, 12)    # 08:00 -> 12:00            (look for setup; "9am NY")
FLATTEN_HOUR = 16     # force-close any open trade by 16:00 ET

# ---- model params ----
SWEEP_BUFFER_BP = 0.0     # extra ticks beyond the level to count as a sweep (bps)
STOP_BUFFER_BP = 2.0      # stop placed this many bps beyond the sweep extreme
FVG_LOOKAHEAD = 15        # bars after the sweep to find a reversal FVG
MIN_RISK_BP = 5.0         # discard degenerate setups whose stop is < this (bps)
RR_TARGET = 2.0           # poster's fixed reward:risk
FEE_BP_PER_SIDE = 7.5     # taker fee + slippage per side, bps (~Binance taker)


@dataclass
class Trade:
    date: str
    symbol: str
    side: str          # long / short
    level: str         # which liquidity pool was swept
    entry_t: str
    entry: float
    stop: float
    target: float
    exit_t: str
    exit: float
    outcome: str       # target / stop / flatten
    r_gross: float     # realised R multiple before costs
    r: float           # realised R multiple, net of cost
    mfe_R: float       # max favourable excursion (R) before stop/EOD — target-independent
    stopped_path: bool # did the stop get hit on the full forward path?
    close_R: float     # R if held to end-of-window (used when target unreached & not stopped)
    risk: float        # |entry-stop| at full precision (don't recompute from rounded prices)


def loc(ts):
    """Localize a naive ET timestamp, tolerating DST gaps/overlaps."""
    return ts.tz_localize(ET, nonexistent="shift_forward", ambiguous=True)


def session_range(day_df, lo_h, hi_h):
    """High/low over an ET clock window [lo_h, hi_h) within one ET 'day frame'."""
    if not len(day_df):
        return None, None
    return float(day_df["high"].max()), float(day_df["low"].min())


def find_fvg(bars, start_i, side):
    """Find the first 3-candle fair value gap in `side` direction at/after start_i.

    Bullish FVG: low[i] > high[i-2]  (gap up) -> displacement up.
    Bearish FVG: high[i] < low[i-2]  (gap down) -> displacement down.
    Entry = the edge of the gap nearest the reversal (conservative fill on the
    displacement candle close-side). Returns (entry_idx, entry_price) or None.
    """
    h = bars["high"].values
    l = bars["low"].values
    c = bars["close"].values
    n = len(bars)
    end = min(n, start_i + FVG_LOOKAHEAD)
    for i in range(start_i + 2, end):
        if side == "long" and l[i] > h[i - 2]:
            return i, float(c[i])
        if side == "short" and h[i] < l[i - 2]:
            return i, float(c[i])
    return None


def resolve_path(side, entry, stop, risk, fwd):
    """Walk the full forward path (ignoring any target) to record, independent of
    the chosen target: max favourable excursion in R, whether the stop was hit,
    and the R if held to end of window. Stop is checked before favourable extreme
    each bar (conservative: same-bar ambiguity resolves against us)."""
    mfe_R = 0.0
    stopped = False
    for _, frow in fwd.iterrows():
        if side == "short":
            if frow["high"] >= stop:
                stopped = True
                break
            mfe_R = max(mfe_R, (entry - frow["low"]) / risk)
        else:
            if frow["low"] <= stop:
                stopped = True
                break
            mfe_R = max(mfe_R, (frow["high"] - entry) / risk)
    last = float(fwd.iloc[-1]["close"]) if len(fwd) else entry
    close_R = ((entry - last) if side == "short" else (last - entry)) / risk
    return round(mfe_R, 3), stopped, round(close_R, 3)


def opposite_dol(side, levels, prev_day):
    """Commenter's 'next draw on liquidity': the opposite-side pool to aim at."""
    if side == "long":  # we swept a low, target liquidity above
        cands = [levels["asia_high"], levels["london_high"]]
        if prev_day:
            cands.append(prev_day[0])
        return max(cands)
    cands = [levels["asia_low"], levels["london_low"]]
    if prev_day:
        cands.append(prev_day[1])
    return min(cands)


def run(df, symbol, exit_mode):
    df = df.tz_convert(ET) if df.index.tz else df.tz_localize("UTC").tz_convert(ET)
    df = df.sort_index()
    # 'day frame' anchored to ET calendar date of the NY session.
    et_date = df.index.normalize()
    trades = []
    prev_levels_extent = None  # (prev_day_high, prev_day_low) for DOL targeting

    for day, _ in df.groupby(et_date):
        d0 = day.date() if hasattr(day, "date") else pd.Timestamp(day).date()
        cur = pd.Timestamp(d0)
        # Asia window = 19:00 (prev day) -> 00:00 (this day)
        asia_start = loc((cur - pd.Timedelta(days=1)).replace(hour=ASIA[0]))
        asia_end = loc(cur)  # 00:00
        london_start = loc(cur.replace(hour=LONDON[0]))
        london_end = loc(cur.replace(hour=LONDON[1]))
        ny_start = loc(cur.replace(hour=NY_TRADE[0]))
        ny_end = loc(cur.replace(hour=NY_TRADE[1]))
        flatten = loc(cur.replace(hour=FLATTEN_HOUR))

        asia = df[(df.index >= asia_start) & (df.index < asia_end)]
        london = df[(df.index >= london_start) & (df.index < london_end)]
        ny = df[(df.index >= ny_start) & (df.index < flatten)]
        ah, al = session_range(asia, *ASIA)
        lh, ll = session_range(london, LONDON[0], LONDON[1])
        if None in (ah, al, lh, ll) or len(ny) < 5:
            continue
        levels = {"asia_high": ah, "asia_low": al, "london_high": lh, "london_low": ll}

        # Scan the NY window minute by minute for the FIRST sweep + reversal FVG.
        ny_trade = ny[ny.index < ny_end]
        idx = ny.index
        done = False
        for ts, row in ny_trade.iterrows():
            if done:
                break
            hi, lo, cl = row["high"], row["low"], row["close"]
            for lname, lval in levels.items():
                is_high = lname.endswith("high")
                buf = lval * SWEEP_BUFFER_BP / 1e4
                # Sweep = wick beyond the level but close back on the origin side.
                swept_high = is_high and hi > lval + buf and cl < lval
                swept_low = (not is_high) and lo < lval - buf and cl > lval
                if not (swept_high or swept_low):
                    continue
                side = "short" if swept_high else "long"
                start_i = ny.index.get_loc(ts)
                fvg = find_fvg(ny, start_i, side)
                if not fvg:
                    continue
                ei, entry = fvg
                # Stop at the sweep extreme (+buffer); risk = |entry-stop|.
                if side == "short":
                    sweep_ext = hi
                    stop = sweep_ext * (1 + STOP_BUFFER_BP / 1e4)
                    risk = stop - entry
                    if risk < entry * MIN_RISK_BP / 1e4:
                        continue
                    if exit_mode == "dol":
                        target = opposite_dol(side, levels, prev_levels_extent)
                    else:
                        target = entry - RR_TARGET * risk
                else:
                    sweep_ext = lo
                    stop = sweep_ext * (1 - STOP_BUFFER_BP / 1e4)
                    risk = entry - stop
                    if risk < entry * MIN_RISK_BP / 1e4:
                        continue
                    if exit_mode == "dol":
                        target = opposite_dol(side, levels, prev_levels_extent)
                    else:
                        target = entry + RR_TARGET * risk

                # Walk forward from entry bar to resolve target/stop/flatten.
                fwd = ny.iloc[ei + 1:]
                mfe_R, stopped_path, close_R = resolve_path(side, entry, stop, risk, fwd)
                outcome, exit_px, exit_t = "flatten", float(ny.iloc[-1]["close"]), ny.index[-1]
                for ft, frow in fwd.iterrows():
                    if side == "short":
                        if frow["high"] >= stop:
                            outcome, exit_px, exit_t = "stop", stop, ft; break
                        if frow["low"] <= target:
                            outcome, exit_px, exit_t = "target", target, ft; break
                    else:
                        if frow["low"] <= stop:
                            outcome, exit_px, exit_t = "stop", stop, ft; break
                        if frow["high"] >= target:
                            outcome, exit_px, exit_t = "target", target, ft; break
                # Realised R, net of round-trip cost expressed in R units.
                gross = (entry - exit_px) if side == "short" else (exit_px - entry)
                cost = (entry + exit_px) * FEE_BP_PER_SIDE / 1e4
                r_gross = gross / risk
                r = (gross - cost) / risk
                trades.append(Trade(
                    str(d0), symbol, side, lname, str(ts), round(entry, 5),
                    round(stop, 5), round(target, 5), str(exit_t), round(exit_px, 5),
                    outcome, round(r_gross, 3), round(r, 3),
                    mfe_R, stopped_path, close_R, risk,
                ))
                done = True
                break
        # update prev-day extent for DOL targeting next day
        full_day = df[(df.index >= loc(cur)) &
                      (df.index < loc(cur + pd.Timedelta(days=1)))]
        if len(full_day):
            prev_levels_extent = (float(full_day["high"].max()), float(full_day["low"].min()))
    return trades


def summarise(trades, label):
    if not trades:
        print(f"\n[{label}] no trades")
        return
    t = pd.DataFrame([asdict(x) for x in trades])
    n = len(t)
    wins = t[t.r > 0]
    losses = t[t.r <= 0]
    wr = len(wins) / n
    avg_r = t.r.mean()
    tot_r = t.r.sum()
    pf = wins.r.sum() / abs(losses.r.sum()) if len(losses) and losses.r.sum() != 0 else float("inf")
    # equity in R and max drawdown in R
    eq = t.r.cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"\n========== {label} ==========")
    print(f"trades        {n}")
    print(f"win rate      {wr:6.1%}   ({len(wins)}W / {len(losses)}L)")
    print(f"gross E/trade {t.r_gross.mean():+.3f} R  (total {t.r_gross.sum():+.1f} R)  <- no fees")
    print(f"net   E/trade {avg_r:+.3f} R  (total {tot_r:+.1f} R)  <- {FEE_BP_PER_SIDE}bp/side")
    print(f"profit factor {pf:.2f}")
    print(f"max drawdown  {dd:.1f} R")
    by = t.outcome.value_counts().to_dict()
    print(f"outcomes      {by}")
    return t


def target_sweep(trades, label):
    """For each candidate target R, derive the outcome from each trade's recorded
    MFE/stop/close — no re-simulation needed (entry & stop are target-independent).
    The win-rate column = fraction of trades that ever reach that R favourable."""
    if not trades:
        print(f"\n[{label}] no trades")
        return
    t = pd.DataFrame([asdict(x) for x in trades])
    risk = t.risk
    grid = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
    print(f"\n========== target sweep — {label}  (n={len(t)}) ==========")
    print(f"{'targetR':>7} {'hit%':>6} {'grossE':>8} {'grossTot':>9} {'netE':>8} {'netTot':>9}")
    for T in grid:
        hit = t.mfe_R >= T                      # target reached before stop
        gross = pd.Series(0.0, index=t.index)
        gross[hit] = T
        # not hit: stop -> -1R ; else held to close -> close_R
        nothit = ~hit
        gross[nothit & t.stopped_path] = -1.0
        gross[nothit & ~t.stopped_path] = t.close_R[nothit & ~t.stopped_path]
        # exit price per case, for cost
        exit_px = t.entry.copy()
        exit_px[hit & (t.side == "long")] = (t.entry + T * risk)[hit & (t.side == "long")]
        exit_px[hit & (t.side == "short")] = (t.entry - T * risk)[hit & (t.side == "short")]
        exit_px[nothit & t.stopped_path] = t.stop[nothit & t.stopped_path]
        flat = nothit & ~t.stopped_path
        exit_px[flat] = (t.entry + gross * risk * t.side.map({"long": 1, "short": -1}))[flat]
        cost_R = (t.entry + exit_px) * FEE_BP_PER_SIDE / 1e4 / risk
        net = gross - cost_R
        print(f"{T:>7.2f} {hit.mean():>5.0%} {gross.mean():>+8.3f} {gross.sum():>+9.1f}"
              f" {net.mean():>+8.3f} {net.sum():>+9.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--exit", choices=["rr", "dol", "both"], default="both")
    ap.add_argument("--sweep", action="store_true", help="sweep target R levels")
    ap.add_argument("--fee", type=float, default=None, help="override fee bp/side")
    ap.add_argument("--save", default="")
    args = ap.parse_args()
    if args.fee is not None:
        global FEE_BP_PER_SIDE
        FEE_BP_PER_SIDE = args.fee
    df = pd.read_parquet(args.parquet)
    sym = args.parquet.split("/")[-1].split("_")[0]
    print(f"loaded {len(df)} bars  {df.index[0]} -> {df.index[-1]}  ({sym})")
    if args.sweep:
        target_sweep(run(df, sym, "rr"), sym)
        return
    modes = ["rr", "dol"] if args.exit == "both" else [args.exit]
    all_t = []
    for m in modes:
        trades = run(df, sym, m)
        label = "fixed 1:2 R (poster)" if m == "rr" else "draw-on-liquidity (commenter)"
        t = summarise(trades, label)
        if t is not None:
            t["exit_mode"] = m
            all_t.append(t)
    if args.save and all_t:
        pd.concat(all_t).to_csv(args.save, index=False)
        print(f"\nsaved trades -> {args.save}")


if __name__ == "__main__":
    main()

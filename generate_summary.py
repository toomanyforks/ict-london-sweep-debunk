"""Emit results/summary.csv — the headline numbers in one skimmable table.

Reuses backtest.run() so the CSV can never drift from the reported results.
Rows: the target-R sweep for each market (with the random-walk benchmark), then
the two literal exit variants from the clip (fixed 1:2 R and draw-on-liquidity).
"""
import csv

import pandas as pd

import backtest as bt

GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
INSTR = [
    ("data/BTCUSDT_1m.parquet", "BTCUSDT", "crypto", "2024-01..2025-06", 7.5),
    ("data/ETHUSDT_1m.parquet", "ETHUSDT", "crypto", "2024-01..2025-06", 7.5),
    ("data/EURUSD_1m.parquet", "EURUSD", "fx", "2024-06..2025-06", 0.3),
]


def sweep_rows(t, fee):
    """(target_R, hit%, gross_E, net_E) for each target, from recorded MFE."""
    risk = t.risk
    out = []
    for T in GRID:
        h = t.mfe_R >= T
        gross = pd.Series(0.0, index=t.index)
        gross[h] = T
        nh = ~h
        gross[nh & t.stopped_path] = -1.0
        gross[nh & ~t.stopped_path] = t.close_R[nh & ~t.stopped_path]
        exit_px = t.entry.copy()
        exit_px[h & (t.side == "long")] = (t.entry + T * risk)[h & (t.side == "long")]
        exit_px[h & (t.side == "short")] = (t.entry - T * risk)[h & (t.side == "short")]
        exit_px[nh & t.stopped_path] = t.stop[nh & t.stopped_path]
        flat = nh & ~t.stopped_path
        exit_px[flat] = (t.entry + gross * risk * t.side.map({"long": 1, "short": -1}))[flat]
        cost = (t.entry + exit_px) * fee / 1e4 / risk
        net = gross - cost
        out.append((T, round(h.mean(), 3), round(gross.mean(), 4), round(net.mean(), 4)))
    return out


def variant(df, sym, mode, fee):
    bt.FEE_BP_PER_SIDE = fee
    t = pd.DataFrame([vars(x) for x in bt.run(df, sym, mode)])
    wr = (t.r > 0).mean()
    pf = t.r[t.r > 0].sum() / abs(t.r[t.r <= 0].sum())
    return round(wr, 3), round(t.r_gross.mean(), 4), round(t.r.mean(), 4), round(pf, 3)


rows = []
for path, sym, market, period, fee in INSTR:
    df = pd.read_parquet(path)
    bt.FEE_BP_PER_SIDE = fee
    t = pd.DataFrame([vars(x) for x in bt.run(df, sym, "rr")])
    n = len(t)
    for T, hit, ge, ne in sweep_rows(t, fee):
        rows.append({
            "market": market, "symbol": sym, "period": period, "trades": n,
            "analysis": "target_sweep", "target_R": T, "hit_rate": hit,
            "random_walk_1_over_1+T": round(1 / (1 + T), 3),
            "gross_E_per_trade_R": ge, "net_E_per_trade_R": ne,
            "fee_bp_per_side": fee, "win_rate": "", "profit_factor": "",
        })
    for mode, name in [("rr", "exit:fixed_1:2R"), ("dol", "exit:draw_on_liquidity")]:
        wr, ge, ne, pf = variant(df, sym, mode, fee)
        rows.append({
            "market": market, "symbol": sym, "period": period, "trades": n,
            "analysis": name, "target_R": "", "hit_rate": "",
            "random_walk_1_over_1+T": "", "gross_E_per_trade_R": ge,
            "net_E_per_trade_R": ne, "fee_bp_per_side": fee,
            "win_rate": wr, "profit_factor": pf,
        })

cols = ["market", "symbol", "period", "trades", "analysis", "target_R", "hit_rate",
        "random_walk_1_over_1+T", "gross_E_per_trade_R", "net_E_per_trade_R",
        "win_rate", "profit_factor", "fee_bp_per_side"]
with open("results/summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
print(f"wrote results/summary.csv ({len(rows)} rows)")

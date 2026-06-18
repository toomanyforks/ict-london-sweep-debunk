"""Generate the two headline charts for the writeup:
  1. target hit-rate vs the random-walk benchmark 1/(1+T)  (the edge test)
  2. net expectancy per trade by target

Reads the cached parquets and reuses backtest.run() so the figures cannot drift
from the reported tables.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import backtest as bt

GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
INSTR = [
    ("data/BTCUSDT_1m.parquet", "BTCUSDT", 7.5, "#f2a900"),
    ("data/ETHUSDT_1m.parquet", "ETHUSDT", 7.5, "#627eea"),
    ("data/EURUSD_1m.parquet", "EURUSD", 0.3, "#1f9d55"),
]


def stats(path, sym, fee):
    bt.FEE_BP_PER_SIDE = fee
    t = pd.DataFrame([vars(x) for x in bt.run(pd.read_parquet(path), sym, "rr")])
    risk = t.risk
    hit, nete = [], []
    for T in GRID:
        h = t.mfe_R >= T
        hit.append(h.mean())
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
        nete.append((gross - cost).mean())
    return np.array(hit), np.array(nete), len(t)


rand = [1 / (1 + T) for T in GRID]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
ax1.plot(GRID, rand, "k--", lw=2, label="random walk  1/(1+T)  (no edge)")
for path, sym, fee, c in INSTR:
    hit, nete, n = stats(path, sym, fee)
    ax1.plot(GRID, hit, "o-", color=c, label=f"{sym}  (n={n})")
    ax2.plot(GRID, nete, "o-", color=c, label=f"{sym} ({fee}bp/side)")
ax1.set_title("Does the entry beat a coin flip?\nTarget hit-rate vs random-walk benchmark")
ax1.set_xlabel("profit target (R)")
ax1.set_ylabel("P(reach target before stop)")
ax1.legend()
ax1.grid(alpha=0.3)
ax2.axhline(0, color="k", lw=1)
ax2.set_title("Net expectancy per trade by target\n(every level, every market: below zero)")
ax2.set_xlabel("profit target (R)")
ax2.set_ylabel("net expectancy (R / trade)")
ax2.legend()
ax2.grid(alpha=0.3)
fig.tight_layout()
fig.savefig("results/edge_test.png", dpi=120)
print("wrote results/edge_test.png")

# Does the "Asia/London sweep → FVG reversal" day-trade actually work?

A reproducible backtest of a popular social-media day-trading claim, on 1-minute
data across crypto (BTC, ETH) and spot FX (EUR/USD).

> **TL;DR.** The strategy's *entry* hits its targets at essentially the
> random-walk rate — it has no measurable directional edge. No choice of profit
> target rescues it, and realistic transaction costs make it firmly negative.
> Full numbers, benchmark, and reproduction steps below.

---

## 1. The claim

The strategy, stated verbatim in the source clip (a creator describing it, and a
commenter "improving" it):

> "Just wake up at 9 AM New York time, mark out the Asia and London high/low,
> wait for those highs and lows to sweep, and drop down to the one-minute time
> frame, take a fair-value-gap reversal to the other side, and target a one-to-two
> RR." … "The only thing I would change is instead of just targeting a 1:2 RR,
> use market structure, target draws on liquidity. … I can attest this does make
> you a lot of money."

Mechanically:

1. Mark the **Asia-session** and **London-session** high and low.
2. Wait for one of those levels to be **swept** — price wicks through it and
   closes back (a stop-hunt / liquidity grab).
3. On the **1-minute** chart, enter on the **fair-value-gap (FVG)** that forms as
   price reverses. Swept a high → **short**; swept a low → **long**.
4. **Stop** at the sweep extreme.
5. **Target**: a fixed **1:2 R** (creator) or the **next draw on liquidity**
   (commenter's variant).

This is a well-defined, fully testable rule set. So we tested it.

## 2. Data

| Market | Symbol | Source | Period | Bars |
|---|---|---|---|---|
| Crypto | BTCUSDT | Binance public archive (`data.binance.vision`) | 2024-01 → 2025-06 | 787,680 |
| Crypto | ETHUSDT | Binance public archive | 2024-01 → 2025-06 | ~787,000 |
| FX | EUR/USD | Dukascopy tick datafeed → 1-min midpoint | 2024-06 → 2025-06 | _(see results)_ |

All bars are 1-minute OHLC. FX uses the bid/ask **midpoint** (matching how the
levels would be marked); crypto uses traded prices. Sessions are evaluated in
**America/New_York** wall-clock with daylight-saving handled, because the
"Asia / London / NY" framing is a clock-based, ET-centric convention.

Why both crypto and FX? The strategy is an FX/futures-native idea (real session
opens), but it is widely pitched on crypto. Testing both separates "the idea is
wrong" from "the idea is wrong *for this market*."

## 3. Method

Session windows (ET, configurable in `backtest.py`):

- **Asia** 19:00 (prev day) → 00:00 · **London** 02:00 → 08:00 · **NY trade window** 08:00 → 12:00, force-flat by 16:00.

For each day we mark the four session levels, then scan the NY window minute by
minute for the **first** level that is swept (wick beyond it, close back on the
origin side). On a sweep we look up to 15 bars ahead for a reversal **FVG** (a
3-candle gap: `low[i] > high[i-2]` bullish / `high[i] < low[i-2]` bearish) and
enter at that candle's close, stop beyond the sweep extreme.

**The key analysis — target sweep.** A single trade's entry, stop, and direction
do not depend on the chosen profit target. So instead of hard-coding 1:2, we
record each trade's **maximum favourable excursion (MFE)** — how far it ran in
our favour before the stop was hit — and then evaluate *every* target from 0.5R
to 5R analytically. The win-rate at target `T` is simply the fraction of trades
whose MFE reached `T`.

**The benchmark that matters.** For a directionless random walk with the stop at
−1R, the probability of reaching +`T`R before being stopped is `1 / (1 + T)`.
If the entry carries *real* directional edge, observed hit-rates must sit
**above** that line. If they sit on it, the entry is a coin flip and no exit rule
can produce a profit. This is the decisive test, and it is cost-independent.

**Costs.** Reported both gross and net. Net uses 7.5 bp/side for retail crypto
taker and ~0.3 bp/side for EUR/USD at an institutional-grade broker. A
minimum-stop filter (`MIN_RISK_BP`) discards degenerate sub-pip setups so the net
figure is not dominated by a handful of near-zero-risk trades.

## 4. Results

### Crypto — no edge at any target

Target sweep, BTCUSDT (n≈486) and ETHUSDT (n≈484), 18 months. `hit%` is the
empirical chance of reaching that target; `rand` is the random-walk benchmark
`1/(1+T)`.

| target | BTC hit% | ETH hit% | rand `1/(1+T)` | BTC gross E | ETH gross E |
|---:|---:|---:|---:|---:|---:|
| 0.5R | 62% | 65% | 67% | −0.08 R | −0.03 R |
| 1.0R | 45% | 46% | 50% | −0.09 R | −0.08 R |
| 2.0R | 30% | 29% | 33% | −0.07 R | −0.13 R |
| 3.0R | 21% | 22% | 25% | −0.08 R | −0.09 R |
| 5.0R | 14% | 13% | 17% | +0.01 R | −0.12 R |

Observed hit-rates sit **on or just below** the random-walk line at every target.
Gross expectancy is negative across the entire curve — there is no profit target
that makes the strategy work. Net of fees it is strongly negative (the stops are
tiny relative to crypto trading costs). **The entry has no directional edge.**

### FX (EUR/USD) — the fair test

Spot FX is the strategy's native habitat: real session opens and ~25× lower
trading cost than crypto. 12 months of 1-minute EUR/USD (Dukascopy), **n = 213
trades**.

| target | hit% | rand `1/(1+T)` | gross E | net E (0.3bp/side) |
|---:|---:|---:|---:|---:|
| 0.5R | 62% | 67% | −0.057 R | −0.138 R |
| 1.0R | 48% | 50% | −0.020 R | −0.100 R |
| 2.0R | 31% | 33% | +0.028 R | −0.053 R |
| 2.5R | 25% | 29% | +0.038 R | −0.043 R |
| 3.0R | 20% | 25% | +0.041 R | −0.039 R |
| 4.0R | 13% | 20% | +0.040 R | −0.040 R |
| 5.0R |  7% | 17% | −0.023 R | −0.104 R |

The hit-rate is **at or below** the random-walk line at every target — at far
targets it is *worse* than random, i.e. the entry shows no directional skill.
Gross expectancy is marginally positive at 2–4R, but that is a payoff-skew
artifact (a few EOD winners), not prediction — and it is **smaller than the most
generous real-world cost.** Net of 0.3 bp/side, **every target loses.**

The literal recipes:

| variant | win rate | gross E | net E | profit factor |
|---|---:|---:|---:|---:|
| fixed 1:2 R (creator) | 36.2% | +0.028 R | **−0.053 R** | 0.92 |
| draw-on-liquidity (commenter) | 25.8% | −0.015 R | **−0.096 R** | 0.87 |

> A 7-week pilot of this same FX test *hinted* at a positive edge at 3R. It
> evaporated at 12 months — a textbook small-sample artifact, and exactly why the
> decisive run uses a full year. We report it because hiding it would be dishonest.

![Edge test](results/edge_test.png)

*Left: every market's target hit-rate sits on or below the no-edge line. Right:
net expectancy per trade is below zero at every target in every market.*

## 5. Conclusion

Across **1,000+ trades** in three markets and two asset classes, the "Asia/London
sweep → 1-minute FVG reversal" entry hits its profit targets at **essentially the
random-walk rate** — it carries no measurable directional edge. Because the entry
is a coin flip, **no profit target makes it profitable**: the target sweep is
flat-to-negative gross everywhere, and **net of realistic costs every target in
every market loses money.** The two exit rules named in the source clip — a fixed
1:2 and "draw on liquidity" — both have profit factors below 1.

The single most-cited evidence for the claim ("I can attest this makes you a lot
of money") is not reproducible. The most favorable market we could construct for
it — spot EUR/USD, where sessions are real and costs are tiny — still loses at
every target over a full year.

This does not prove *no* discretionary trader can make money around session
liquidity. It shows that the **specific, mechanical recipe as taught** has no
edge, and that its profitability claim does not survive contact with data.

## 6. Reproduce it yourself

```bash
pip install pandas pyarrow ib_insync   # ib_insync only for the IBKR path

# Crypto (free, no auth):
python3 fetch_binance.py BTCUSDT ETHUSDT --start 2024-01 --end 2025-06
python3 backtest.py data/BTCUSDT_1m.parquet --sweep

# FX (free, no auth — Dukascopy tick archive):
python3 fetch_dukascopy.py EURUSD --start 2024-06-01 --end 2025-06-01
python3 backtest.py data/EURUSD_1m.parquet --sweep --fee 0.3

# Both literal exit variants (fixed 1:2 R and draw-on-liquidity):
python3 backtest.py data/EURUSD_1m.parquet --exit both --fee 0.3
```

All parameters (session windows, FVG lookahead, stop buffer, fees, min-stop
filter) are constants at the top of `backtest.py`. Change them and re-run; the
conclusion is robust to reasonable choices.

## 7. Honest caveats

- This is **one** faithful mechanisation. "Sweep," "FVG," and "draw on liquidity"
  admit discretionary variation; a human trader may filter setups we take.
- We take the first qualifying setup per day in the NY window, with no
  higher-timeframe bias filter and a market fill at the FVG candle close.
- These choices affect *magnitude*, not the core finding: the entry's target
  hit-rate tracks `1/(1+T)`, which no exit rule or fill improvement can overcome.
- Past data, single symbols per market, finite windows. Reproduce and extend.

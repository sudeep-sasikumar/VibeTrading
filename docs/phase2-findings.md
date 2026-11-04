# Phase 2 findings (2026-11-04)

Goal: two swing strategies, a backtest command, and stats reported for the
whole run, in-sample and out-of-sample, on all six universes.

## What was built

- `vibetrading/strategies.py`: `Momentum` and `Pullback` as Vibe-Trading
  `SignalEngine` classes that output target weights.
- `vibetrading/backtest.py`: loads prices from the store, cleans them, builds
  the "tradeable" mask, runs the market's engine in-process and saves the
  results.
- `vibetrading/breaks.py`: splits price series at fake one-day breaks.
- `vibetrading/metrics.py`: CAGR, max drawdown, Sharpe, trades, win rate,
  holding days, turnover and fees per period, plus the benchmark over the same
  dates.
- `config.yaml`: periods, starting cash, engine settings, strategy parameters.
- `python -m vibetrading backtest --strategy <name> [universe ...]`.
- 25 unit tests.

## How a backtest works

- **Execution:** a weight decided at a bar's close is traded at the next bar's
  open (the engine's rule). Weights are 1/10 per position, so unfilled slots
  stay in cash.
- **Who can be held:** only a symbol that is a universe member that day and
  inside its price history. Weight drops to 0 the day it leaves the index or
  the top 200, so it is sold at the next open.
- **Delisted or merged symbols** are sold at the open of their last price bar
  (weight 0 at the close before). Otherwise the engine could never sell them.
  This assumes the end is seen one day early, which slightly flatters a
  collapse.
- **Costs** use the engine's per-market defaults:
  - US: no commission, 0.05% slippage per side. Slippage is in the fill
    prices, so the "fees" column shows 0% for US.
  - India: STT 0.1% each side, stamp duty, exchange, SEBI and GST charges,
    plus 0.1% slippage.
  - Crypto: 0.05% taker fee and 0.05% slippage. Funding is set to 0, because
    the crypto engine models perpetual futures and otherwise charges 0.01%
    every 8 hours, about 11% a year, even on spot. The Phase 0 crypto spike
    carried that charge.
- **Rebalance:** positions are resized only when a weight drifts more than 25%
  from its target.
- **Periods:**
  - Stocks: 2015 to now, split at 2022-01-01.
  - Crypto: 2021 to now, split at 2024-01-01. Before 2021 the top-200 had
    fewer than 100 coins (Phase 1).

## Data problems found and fixed in this phase

One-day price breaks that no holder experienced:

| Symbol | Date | Move | What it is |
|---|---|---|---|
| LUNA-USDT | 2022-05-31 | ×177,400 | Ticker reuse: new LUNA took the old pair |
| COCOS-USDT | 2021-01-23 | ×1,295 | Redenomination |
| DREP-USDT | 2021-04-02 | ×108 | Token swap |
| SUN-USDT / BNX-USDT / QUICK-USDT | 2021-06-18 / 2023-02-22 / 2023-07-21 | ÷833 / ÷111 / ÷1,243 | Redenominations: coin volume jumped 1,000×+ that day |
| GPIL.NS | 2024-01-01 | ÷4.9 | Looks like an unadjusted split on Yahoo |
| PATANJALI.NS | 2020-01-27 | ÷19.7 | Ruchi Soya's relisting after insolvency |

Each is now cut into two instruments at the break, and listed in the run's
output. Crypto drops count as breaks only when coin volume jumps more than
500× the same day. That keeps LUNA's real crash on 2022-05-12 (÷3,365, volume
~100×) as a real loss. Rule thresholds: crypto ×20 up / ÷20 down with the
volume test; stocks ×3 up / ÷4 down. None of the six crypto breaks changed the
momentum result, because it never held those coins across a break.

## Results

CAGR / max drawdown. "OOS" is 2022 on for stocks, 2024 on for crypto, and was
never used to choose settings. Settings are untouched defaults; nothing has
been tuned yet.

| Universe | Momentum full | Momentum OOS | Pullback full | Pullback OOS | Benchmark full | Benchmark OOS |
|---|---|---|---|---|---|---|
| S&P 500 | 11.2% / -41.6% | 14.6% / -41.6% | 13.1% / -24.1% | 13.8% / -22.8% | SPY 13.5% / -33.7% | 11.4% / -24.5% |
| NASDAQ-100 | 18.7% / -49.2% | 9.3% / -43.5% | 11.8% / -24.7% | 13.1% / -17.3% | QQQ 18.2% / -35.1% | 12.4% / -34.8% |
| Nifty 50 † | 19.1% / -35.7% | 12.1% / -24.2% | -0.8% / -34.1% | -2.8% / -24.1% | 9.3% / -38.4% | 6.6% / -16.5% |
| Nifty Midcap 150 † | 28.1% / -41.5% | 28.5% / -29.8% | 7.8% / -44.1% | 7.5% / -25.1% | 17.4%* / -38.7% | 13.9% / -21.1% |
| Nifty Smallcap 250 † | 35.4% / -42.2% | 23.7% / -39.5% | 14.7% / -51.3% | 15.3% / -35.5% | 12.7% / -60.8% | 12.2% / -26.9% |
| Crypto Top 200 | -29.1% / -98.0% | -55.1% / -93.8% | -17.9% / -86.1% | -33.5% / -72.2% | BTC 18.1% / -76.6% | 21.9% / -53.0% |

† Today's members held back to 2015: survivorship bias makes these too good,
by an unknown amount. \* Midcap benchmark prices start 2019-01-14.

## What the numbers say

1. **Nothing here is a proven edge yet.** The only universes with honest
   membership are the US ones (crypto too, but it lost). There, neither
   strategy clearly beats just holding SPY or QQQ.
2. **Pullback on US stocks is the most promising result.** It roughly
   matches the index's return with about two-thirds of its drawdown. On
   NASDAQ-100 out-of-sample: 13.1% a year with a -17.3% worst drop, against
   QQQ's 12.4% and -34.8%. It wins 64–66% of trades and holds about 7 days.
   Caveat: in 2015 a quarter of the US members have no Yahoo prices (Phase 1),
   so the US results are flattered too, just less than India's.
3. **Momentum on US stocks** made 11% (S&P) and 19% (NASDAQ) a year, about
   the index, with deeper drawdowns (-42% / -49%).
4. **Indian trading costs kill high-turnover strategies.** Pullback turns its
   book over 25–43 times a year. With 0.1% STT on each side, fees alone came
   to 65% (Nifty 50) to 113% (Smallcap 250) of average equity over the 12
   years. Nifty 50 pullback made -0.8% a year. Momentum trades a third as much
   and keeps most of its return.
5. **The Nifty momentum numbers (19–35% a year) can't be trusted.** They come
   from today's winners. They need historical index membership before they
   mean anything.
6. **Crypto: both strategies lose badly; BTC buy-and-hold made 18% a year.**
   Momentum turned $100k into $610k during 2021, then gave it all back through
   the FTX collapse (-36% on 2022-11-09) and years of altcoins falling against
   BTC. It ended at ~$13k. Ranking 200 altcoins by 6-month return and holding
   them through crashes doesn't work on this data. If crypto stays in scope,
   the strategy needs a market filter (only hold alts while BTC is above its
   200-day average) or a smaller universe.

## Speed

One run takes 15 s (Nifty 50) to 4 min (S&P 500 momentum: 750 symbols × 3,000
bars, bar by bar). All 12 take ~17 minutes. Fine for a portal with a job queue,
too slow to sweep many parameter combinations without parallel runs.

## Next

- Phase 3 (portal): one tab per universe showing these results, with the
  survivorship and benchmark warnings visible.
- Strategy work worth doing before trusting any of this: a market-trend
  filter, lower-turnover variants for India, and a parameter sweep run only on
  the in-sample period.

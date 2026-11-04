# Strategy tuning findings (2026-11-04)

Goal: improve the two strategies before building the portal, without fitting
them to luck.

## What changed

New strategy options (all off by default, so `backtest` without `--tuned`
reproduces Phase 2):

- **Market-trend filter** (`regime_filter`): buy only while the universe's
  index closes above its 200-day average (SPY for the S&P 500, QQQ for
  NASDAQ-100, ^NSEI for all Nifty tabs, BTC for crypto). Momentum goes fully to
  cash on a down day; pullback just stops opening new trades.
- **Liquidity filter** (`liquid_top`): buy only the N most traded tradeable
  names by 20-day average traded value. It only filters buys, so a held stock
  isn't sold just because it slipped down the liquidity ranking.
- **Pullback exit**: `exit_rule: ma` sells when the close crosses above its
  5-day average (instead of RSI above 70), and `stop_loss: null` turns the
  stop off.

New tools:

- `vibetrading/fasteval.py`: an approximate backtest in array maths, about
  1 second per run instead of minutes. `python -m vibetrading check` compares
  it with the engine. On the 24 default-setting comparisons, CAGR agreed within
  about 0.5 points in most cases (worst gap 3.6 points, crypto in-sample) and
  Sharpe within about 0.1.
- `python -m vibetrading sweep --strategy <name>`: tries every combination in
  `config.yaml`'s `sweeps` grid (72 momentum and 144 pullback settings per
  universe) and writes the winners to `tuned.yaml`.
- `backtest --tuned` runs those winners through the engine.

## How settings were chosen

The rule was fixed before any result was seen: **the best in-sample Sharpe
among settings that made money in-sample.** In-sample is 2015–2021 for stocks
and 2021–2023 for crypto. Out-of-sample results were computed for every
setting but never used to choose. The sweep also reports how well in-sample
rank predicted out-of-sample rank across the grid (Spearman correlation).
Near zero means picking the in-sample best is close to picking at random.

## Results (engine, out-of-sample, CAGR / max drawdown / Sharpe)

| Universe | Strategy | Default | Tuned | Benchmark | Rank correlation |
|---|---|---|---|---|---|
| S&P 500 | momentum | 14.6% / -41.6% / 0.56 | **23.8% / -31.1% / 0.86** | 11.4% / -24.5% / 0.71 | +0.05 |
| S&P 500 | pullback | 13.8% / -22.8% / 0.71 | **14.4% / -20.7% / 0.90** | 11.4% / -24.5% / 0.71 | +0.01 |
| NASDAQ-100 | momentum | 9.3% / -43.5% / 0.43 | 15.8% / -47.3% / 0.55 | 12.4% / -34.8% / 0.62 | +0.35 |
| NASDAQ-100 | pullback | 13.1% / -17.3% / 0.73 | 7.5% / -20.0% / 0.44 | 12.4% / -34.8% / 0.62 | -0.31 |
| Nifty 50 † | momentum | 12.1% / -24.2% / 0.74 | 7.8% / -22.5% / 0.59 | 6.6% / -16.5% / 0.54 | -0.24 |
| Nifty 50 † | pullback | -2.8% / -24.1% / -0.20 | 0.7% / -17.9% / 0.13 | 6.6% / -16.5% / 0.54 | +0.45 |
| Nifty Midcap 150 † | momentum | 28.5% / -29.8% / 1.12 | 37.0% / -33.5% / 1.41 | 13.9% / -21.1% / 0.87 | +0.79 |
| Nifty Midcap 150 † | pullback | 7.5% / -25.1% / 0.51 | 2.8% / -21.7% / 0.29 | 13.9% / -21.1% / 0.87 | +0.29 |
| Nifty Smallcap 250 † | momentum | 23.7% / -39.5% / 0.89 | 22.5% / -21.6% / 1.15 | 12.2% / -26.9% / 0.73 | +0.65 |
| Nifty Smallcap 250 † | pullback | 15.3% / -35.5% / 0.75 | 12.3% / -36.9% / 0.61 | 12.2% / -26.9% / 0.73 | +0.61 |
| Crypto Top 200 | momentum | -55.1% / -93.8% / -0.69 | -19.7% / -75.9% / -0.11 | BTC 21.9% / -53.0% / 0.66 | +0.53 |
| Crypto Top 200 | pullback | -33.5% / -72.2% / -0.52 | -19.8% / -59.5% / -0.20 | BTC 21.9% / -53.0% / 0.66 | +0.39 |

† Today's members only: survivorship bias, numbers too good by an unknown amount.

Chosen settings are in `tuned.yaml`. The two that matter most:

- **S&P 500 momentum:** 63-day return, top 10, rebalanced monthly, chosen only
  from the 50 most traded members, no market filter.
- **S&P 500 pullback:** RSI(2) below 10 in an uptrend, exit when RSI is above
  70 or after 10 days, no stop, only the 50 most traded members, and only
  while SPY is above its 200-day average.

## What it means

1. **Tuning helped out-of-sample Sharpe in 8 of 12 cases and hurt in 4.**
   Better than chance, but tuning is clearly not a reliable improvement on its
   own.
2. **The best credible result is S&P 500 momentum (tuned):** 23.8% a year
   out-of-sample against SPY's 11.4%, and almost the same in-sample (24.9%), so
   it held up across both periods. Two reasons for caution:
   - In-sample rank barely predicted out-of-sample rank across the grid
     (+0.05). That a good setting was chosen is partly luck.
   - "Top 10 of the 50 most traded" means mega-caps. It rode the 2022–2026
     mega-cap run, and it would suffer if that reverses. Its drawdown (-31%) is
     deeper than SPY's (-24.5%).
3. **S&P 500 pullback (tuned) is the steadiest:** 14.4% a year against
   SPY's 11.4%, a shallower worst drop (-20.7% vs -24.5%) and a Sharpe of 0.90
   vs 0.71. In-sample it was 12.2% with a -12.2% worst drop. Same caution about
   rank correlation (+0.01).
4. **The market-trend filter mostly helps crypto and the slower strategies.**
   It was chosen for both crypto strategies, Smallcap momentum, and S&P and
   Nifty 50 pullback. In crypto it cut the losses roughly in half, but both
   strategies still lose against just holding BTC. **Altcoin rotation doesn't
   work on this data**, tuned or not. Tuned crypto momentum made +101% a year
   in 2021–2023, then -20% a year since. That's the clearest sign here of what
   in-sample numbers are worth.
5. **India:**
   - Pullback is still eaten by costs (Smallcap 250: fees of 123% of average
     equity over 12 years).
   - Midcap and Smallcap momentum look strong, and in-sample rank actually
     predicted out-of-sample rank there (+0.65 to +0.79). But they still use
     today's index members, so they can't be trusted until historical
     membership is fixed.
6. **In-sample numbers of a chosen setting are inflated.** Picking the best of
   72–144 settings guarantees an optimistic in-sample figure. Read only the
   out-of-sample column.

## Practical issues found

- **This PC has ~8 GB of RAM and was down to 0.2–0.7 GB free.** Six parallel
  sweep workers, then two, ran out of memory, and engine runs slowed down 20–40×
  from swapping (crypto pullback: 43 minutes instead of ~2). Fixes:
  - sweeps run one universe at a time by default (`--workers` to parallelise);
  - read-only DuckDB connections are capped at 512 MB;
  - prices load in chunks of 100 symbols.
- **The engine wrote a CSV of every symbol's prices on every run** (750 files
  per S&P run, 2.2 GB under `var/runs` so far). Those duplicate the price
  store, so the run now skips them.

## Suggested next steps

- **Walk-forward test** of the two S&P candidates: re-choose settings each year
  using only the years before it. This tests the selection process instead of
  one lucky split, and the fast evaluator makes it cheap.
- **Historical Nifty membership** (paid data, or rebuilt from NSE index-change
  circulars) before any India result can be trusted.
- **Drop altcoin rotation**, or replace it with a BTC/ETH trend-following
  strategy.

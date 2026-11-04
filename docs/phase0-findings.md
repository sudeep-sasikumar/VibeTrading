# Phase 0 findings (2026-11-04)

Goal: prove that Vibe-Trading's backtest engines can run one strategy on crypto,
US and Indian stocks, decide how we depend on it, and check how far back
CoinGecko history goes.

## Result: all three engines work

Toy strategy (`spike/run_spike.py`): long while the 50-day average is above the
200-day average, 10 tickers per market, 2019-01-01 to 2026-10-31, 200-bar
warm-up, $100k start. Default "hold" mode, benchmark = equal-weight basket of the
same 10 tickers (the engine's default when no benchmark is set).

| Market | Loader | Engine | Run time | Total return | CAGR | Max drawdown | Sharpe | Trades | Basket buy & hold |
|---|---|---|---|---|---|---|---|---|---|
| Crypto | okx | CryptoEngine | 36 s | +628% | 31.3% | -76.2% | 0.74 | 52 | +1,775% |
| US | yahoo | GlobalEquityEngine | 13 s | +598% | 31.9% | -29.9% | 1.20 | 43 | +480% |
| India | yahoo | IndiaEquityEngine | 13 s | +61% | 7.2% | -29.9% | 0.55 | 53 | +144% |

These numbers say nothing about strategy quality: 10 hand-picked survivors and a
toy rule. They only show the plumbing works end to end. Each run writes
`equity.csv`, `trades.csv`, `positions.csv`, `metrics.csv` and a run card, which
is everything the portal needs to draw.

## Decision: depend on the package, installed lean

- `vibe-trading-ai==0.1.18` is on PyPI (MIT).
- Full install: 188 packages, ~900 MB, ~6 min (LangChain agent, China data SDKs,
  PDF/Office tooling).
- Copying only the `backtest/` folder does not work cleanly: it imports
  Vibe-Trading's `src.config` in 23 places.
- Lean install works: `pip install --no-deps vibe-trading-ai==0.1.18`, then
  `requirements.txt` (12 packages). ~514 MB, ~3 min, same results on all three
  markets. **pandas must stay below 3** (the package declares `<3.0`; an
  unpinned lean install picked 3.1).

## Gotchas found

1. **US tickers need a `.US` suffix** (`AAPL.US`). Bare `AAPL` makes the yahoo
   loader return nothing and the engine exits with "No data fetched". India uses
   `.NS`, crypto `BTC-USDT`.
2. **The engine prints and exits instead of raising.** Data errors call
   `sys.exit(1)` after printing JSON, and a successful run prints the full
   metrics JSON to stdout. Our wrapper must catch `SystemExit` and run backtests
   in a worker process so a failure can't kill the web server.
3. **OKX history is short for some coins.** BNB-USDT starts 2022-12-20 on OKX,
   SOL and AVAX in 2020-09. Phase 1 should fetch crypto from Binance via ccxt
   and keep a per-coin first-date report.
4. **Hold mode ignores weight changes.** With the default `hold`, a position
   isn't resized when another signal joins (the run logs these as
   `dropped_target_adjustments`). Real strategies should use
   `position_adjustment: rebalance` with a `rebalance_tolerance` band.
5. **Set the benchmark explicitly** (BTC-USDT, SPY, Nifty 50 index); otherwise
   it is the equal-weight basket of whatever the strategy traded.
6. **Yahoo data shifts between fetches.** Two US runs an hour apart differed
   slightly (+596% vs +598%) as adjusted prices were refreshed. Phase 1 caches
   prices so reruns are reproducible.

## CoinGecko history depth

Keyless public API: requests older than 365 days are rejected ("Public API users
are limited to querying historical data within the past 365 days"); 2025-12-01
worked, 2024-01-01 and 2019-01-01 did not. The Demo key was **not tested**; I
expect the same 365-day limit but haven't confirmed it.

So historical "top 200 by market cap" can't come from free CoinGecko. Proposal
for Phase 1: rank coins each month by trailing 30-day USD trading volume from
exchange candles (point-in-time, free), and use CoinGecko only for today's list.
Exchanges also drop delisted pairs, so the crypto tab carries the same
survivorship warning as the stock tabs.

# Phase 1 findings (2026-11-04)

Goal: membership lists for all six universes, a local price store, and a report
of how much of each universe actually has prices.

## What was built

- `vibetrading/store.py`: one DuckDB file (`var/prices.duckdb`) with daily
  prices, membership spans and a fetch log. Symbols use the engine's format
  (`AAPL.US`, `RELIANCE.NS`, `BTC-USDT`).
- `vibetrading/sources/`: Yahoo (stocks, same split/dividend adjustment as
  Vibe-Trading's own loader), Binance public market data (crypto), and the
  membership files.
- `vibetrading/universes.py`: the six universe definitions, and the monthly
  crypto ranking.
- `vibetrading/coverage.py`: the coverage report, also saved as
  `var/coverage/<universe>.json`.
- `python -m vibetrading refresh` fetches everything in ~4 minutes; `coverage`
  re-prints the report. 15 unit tests (`pytest tests`).

## Where membership comes from

| Universe | Source | Kind |
|---|---|---|
| Crypto Top 200 | Binance USDT pairs, live **and delisted** (699 after removing stablecoins, fiat, gold, wrapped and leveraged tokens) | On the 1st of each month, the 200 coins with the most USDT trading over the previous 30 days (at least 20 of those days traded). Uses only data from before the month. |
| S&P 500 | [unliftedq/index-constitution](https://github.com/unliftedq/index-constitution) (MIT), built from Wikipedia | Point in time, under each company's current ticker |
| NASDAQ-100 | same | Point in time. Counts are securities, so dual share classes (GOOG/GOOGL, FOX/FOXA...) push it above 100 |
| Nifty 50 / Midcap 150 / Smallcap 250 | NSE's published constituent CSVs | **Today's list only.** REITs and NSE's "DUMMY..." demerger placeholders are dropped (Midcap 149, Smallcap 246 remain) |

I chose index-constitution over the better-known fja05680/sp500 because it lists
each company under its current ticker (META for the FB years too), which is
the ticker Yahoo keeps the full history under. Both agree on today's 503 members.

## Coverage: members with prices on 1 January

| Universe | 2015 (2019 crypto) | 2018 | 2021 | 2024 | 2026 | Benchmark from |
|---|---|---|---|---|---|---|
| Crypto Top 200 | 19 / 19 | – | 171 / 171 | 200 / 200 | 200 / 200 | BTC-USDT 2018 |
| S&P 500 | 376 / 496 (76%) | 427 / 502 | 459 / 504 | 482 / 502 | 495 / 503 | SPY 2014 |
| NASDAQ-100 | 73 / 110 (66%) | 85 / 105 | 92 / 102 | 96 / 101 | 99 / 101 | QQQ 2014 |
| Nifty 50* | 43 / 50 | 47 / 50 | 48 / 50 | 50 / 50 | 50 / 50 | ^NSEI 2014 |
| Nifty Midcap 150* | 95 / 149 | 111 / 149 | 120 / 149 | 131 / 149 | 149 / 149 | **2019 only** |
| Nifty Smallcap 250* | 132 / 246 | 148 / 246 | 166 / 246 | 212 / 246 | 239 / 246 | 2014 |

\* Today's members only. "Not priced" there means the company wasn't listed yet.
It doesn't mean data is missing.

## What this means for backtests

1. **US: Yahoo has no prices for 134 S&P 500 and 42 NASDAQ-100 members.**
   These are mostly companies that were acquired or went bankrupt (Celgene,
   Activision, Allergan...). In 2015 a quarter of the S&P 500 can't be traded.
   Survivorship bias is reduced, not removed. Fixing it means a paid source with
   delisted prices (Norgate, EODHD, Sharadar); that's a later decision.
2. **Reused tickers are caught.** 16 old S&P members (SE, ADT, STI, EMC, NE...)
   have Yahoo prices that start years after they left the index, because the
   ticker now belongs to a different company. The report lists them, and Phase 2
   must only trade a member on dates covered by both its membership and its
   prices.
3. **Yahoo stubs.** Some recently delisted companies (WBD, EA, AVB, EQR, CMA...)
   come back with a single day of prices. Anything under 20 rows counts as
   missing.
4. **Crypto Top 200 is small before 2021.** In December 2018 only 19 Binance USDT
   pairs traded most days; the universe reaches 171 coins by 2021 and 200 from
   2022. **Recommendation: start crypto backtests in 2021**, or show the member
   count next to the equity curve.
5. **Crypto includes dead coins** (LUNA, FTT-era pairs, AGIX merged into FET...).
   44 coins have partial history because they were delisted. Phase 2 needs a
   rule for a held coin that stops trading (exit at its last close).
6. **Nifty tabs carry full survivorship bias.** They hold today's winners
   back to 2015, which inflates every result. NSE has no free machine-readable
   history; the portal must label these tabs clearly.
7. **The NIFTYMIDCAP150.NS benchmark on Yahoo starts 2019-01-14.** Midcap
   backtests before 2019 need a different benchmark, or the equal-weight member
   basket.

## Other notes

- pandas 2.3 with NumPy 2.5 warns that `pd.Timedelta(days=...)` will become an
  error; our code uses `pd.Timedelta(n, unit="D")`.
- A full refresh takes ~4 minutes: Binance ~1 min (699 pairs, 6 in parallel),
  Yahoo ~3 min in batches of 80. Crypto refreshes are incremental. Stock
  refreshes refetch everything, because adjusted prices change on every
  dividend.

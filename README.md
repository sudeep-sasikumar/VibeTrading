# VibeTrading

Swing-trading backtest portal built on the backtest engines from
[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (MIT). One tab per
universe: Crypto Top 200, S&P 500, NASDAQ-100, Nifty 50, Nifty Midcap 150,
Nifty Smallcap 250.

Status: Phase 2 (strategies and backtests) done. Findings:
[Phase 0](docs/phase0-findings.md) (engines), [Phase 1](docs/phase1-findings.md) (data),
[Phase 2](docs/phase2-findings.md) (first results).

## Setup

Python 3.12. Install in two steps, so pip skips the LLM agent dependencies the
engines don't use:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install --no-deps vibe-trading-ai==0.1.18
.venv/Scripts/python -m pip install -r requirements.txt
```

`vibe-trading-ai` is kept out of `requirements.txt` on purpose: listed there,
pip would install its full ~900 MB dependency set.

For tests: `.venv/Scripts/python -m pip install -r requirements-dev.txt`, then
`.venv/Scripts/python -m pytest tests`.

## Data

Universes: `crypto200`, `sp500`, `nasdaq100`, `nifty50`, `niftymidcap150`,
`niftysmallcap250`. Prices and membership go to `var/prices.duckdb` (set
`VIBETRADING_VAR` to put `var/` elsewhere).

```bash
.venv/Scripts/python -m vibetrading refresh                 # all six, ~4 min
.venv/Scripts/python -m vibetrading refresh sp500 nifty50   # some
.venv/Scripts/python -m vibetrading coverage                # report only
```

## Backtests

Strategies: `momentum` (weekly rotation into the 10 strongest uptrends) and
`pullback` (buy RSI(2) dips in uptrends, sell the bounce). Settings and the
backtest periods live in [config.yaml](config.yaml).

```bash
.venv/Scripts/python -m vibetrading backtest --strategy momentum sp500
.venv/Scripts/python -m vibetrading backtest --strategy pullback              # all six universes
.venv/Scripts/python -m vibetrading backtest --strategy momentum nifty50 --start 2018-01-01
```

Each run prints CAGR, max drawdown, Sharpe, the benchmark's CAGR and drawdown,
trades, win rate, holding days, turnover and fees, for the whole run, before
the split date (in-sample) and after it (out-of-sample). The engine's files and
`summary.json` are saved under `var/runs/<universe>/<strategy>/<time>/`.

## Phase 0 spike

```bash
.venv/Scripts/python spike/run_spike.py           # crypto, us, india
.venv/Scripts/python spike/run_spike.py india     # one market
```

Results land in `spike/runs/<market>/artifacts/`.

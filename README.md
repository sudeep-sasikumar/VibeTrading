# VibeTrading

Swing-trading backtest portal built on the backtest engines from
[HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (MIT). One tab per
universe: Crypto Top 200, S&P 500, NASDAQ-100, Nifty 50, Nifty Midcap 150,
Nifty Smallcap 250.

Status: Phase 1 (data) done. Findings:
[Phase 0](docs/phase0-findings.md) (engines), [Phase 1](docs/phase1-findings.md) (data).

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

## Phase 0 spike

```bash
.venv/Scripts/python spike/run_spike.py           # crypto, us, india
.venv/Scripts/python spike/run_spike.py india     # one market
```

Results land in `spike/runs/<market>/artifacts/`.

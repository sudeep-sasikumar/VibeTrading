"""Paths and history windows shared by the data layer."""

from __future__ import annotations

import os
from pathlib import Path

# Everything the app writes lives here; on the VPS it is the /app/var volume.
VAR_DIR = Path(os.environ.get("VIBETRADING_VAR", Path(__file__).resolve().parent.parent / "var"))
DB_PATH = VAR_DIR / "prices.duckdb"
SOURCES_DIR = VAR_DIR / "sources"     # raw membership files, kept for audit
COVERAGE_DIR = VAR_DIR / "coverage"

# Backtests start in 2015 (stocks) and 2019 (crypto). Prices are fetched a year
# earlier so a 200-day indicator is warm on the first evaluated bar.
STOCK_HISTORY_START = "2014-01-01"
STOCK_BACKTEST_START = "2015-01-01"
CRYPTO_HISTORY_START = "2018-01-01"
CRYPTO_BACKTEST_START = "2019-01-01"

# Fewer daily rows than this is a placeholder, not a price history.
MIN_PRICE_ROWS = 20

"""Index membership files.

- S&P 500 and NASDAQ-100 history: unliftedq/index-constitution (MIT). It lists
  each company under its *current* ticker for its whole membership (META for
  the FB years too), which is the ticker Yahoo serves the full history under.
- Nifty lists: NSE's published constituent CSVs. These are today's members
  only; NSE has no free machine-readable history.

Raw files are saved under ``var/sources`` so a run can be traced to the exact
membership data it used.
"""

from __future__ import annotations

import io
from datetime import date

import httpx
import pandas as pd

from vibetrading.settings import SOURCES_DIR

INDEX_CONSTITUTION = "https://raw.githubusercontent.com/unliftedq/index-constitution/HEAD/history/{index}.csv"
NSE_LIST = "https://archives.nseindia.com/content/indices/{name}.csv"
# NSE's archive host rejects requests without a browser-like user agent.
NSE_HEADERS = {"User-Agent": "Mozilla/5.0"}


def us_index_history(index: str) -> pd.DataFrame:
    """``index`` is ``sp500`` or ``nasdaq100``. Columns: ticker, name, start_date, end_date."""
    text = _fetch(INDEX_CONSTITUTION.format(index=index), f"{index}_history.csv")
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["ticker", "name", "start_date", "end_date"]
    for col in ("start_date", "end_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def nse_current(list_name: str) -> pd.DataFrame:
    """``list_name`` like ``ind_nifty50list``. Columns: ticker, name."""
    text = _fetch(NSE_LIST.format(name=list_name), f"{list_name}.csv", headers=NSE_HEADERS)
    df = pd.read_csv(io.StringIO(text))
    ticker = df["Symbol"].str.strip()
    # Keep ordinary equity: NSE also lists REITs (series RR) and placeholder
    # "DUMMY..." entries left behind by demergers, which have no price history.
    keep = (df["Series"].str.strip() == "EQ") & ~ticker.str.startswith("DUMMY")
    return pd.DataFrame({"ticker": ticker[keep], "name": df.loc[keep, "Company Name"].str.strip()})


def _fetch(url: str, filename: str, headers: dict | None = None) -> str:
    resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True).raise_for_status()
    text = resp.content.decode("utf-8-sig")
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    (SOURCES_DIR / f"{date.today():%Y%m%d}_{filename}").write_text(text, encoding="utf-8")
    return text

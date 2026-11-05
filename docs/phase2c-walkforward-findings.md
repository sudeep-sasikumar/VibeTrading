# Walk-forward findings (2026-11-05)

Goal: test the *method* of choosing settings, not one lucky split. At the start
of each year from 2018, choose the setting with the best Sharpe over the years
before it (same grid, same rule as the sweep), trade it that year, and repeat.
Run on the two universes with honest point-in-time membership, S&P 500 and
NASDAQ-100, for both strategies.

## How it was done

- `python -m vibetrading walkforward --strategy <name> [universe ...] [--window 3] [--engine]`
  (`vibetrading/walkforward.py`).
- Every setting in the sweep grid (72 momentum, 144 pullback) is evaluated
  once over 2015–2026 with the fast evaluator. Each test year then picks from
  data before it.
- Two kinds of training window:
  - **expanding:** every year since 2015;
  - **rolling:** only the last 3 years.
- Each expanding schedule was **replayed through the engine** (`Scheduled`),
  including the cost of switching settings at a year boundary. The engine
  agreed with the fast numbers: S&P momentum 25.7% vs 26%, NASDAQ momentum
  18.7% vs 17%, S&P pullback 10.3% vs 10%, NASDAQ pullback 11.0% vs 12%.
- Compared with, over the same 2018–2026 test years:
  - **default settings**, never re-chosen;
  - **single-split tuned** (`tuned.yaml`), chosen on 2015–2021, so flattered
    in 2018–2021;
  - **grid average**, every setting held in equal parts, i.e. not choosing;
  - **buy and hold** SPY or QQQ.

## Results, 2018-01-02 to 2026-11-03 (CAGR / max drawdown / Sharpe)

| Universe · strategy | Walk-forward, expanding | Walk-forward, 3-year rolling | Default | Grid average | Benchmark |
|---|---|---|---|---|---|
| **S&P 500 momentum** | **26% / -30% / 0.93** (engine: 25.7% / -31.1% / 0.96) | 19% / -37% / 0.72 | 11% / -44% / 0.47 | 16% / -31% / 0.69 | SPY 14% / -34% / 0.80 |
| NASDAQ-100 momentum | 17% / -53% / 0.61 | 15% / -41% / 0.59 | 17% / -50% / 0.63 | 21% / -37% / 0.76 | QQQ 19% / -35% / 0.85 |
| S&P 500 pullback | 10% / -21% / 0.68 | 16% / -28% / 0.83 | 15% / -25% / 0.76 | 12% / -17% / 0.81 | SPY 14% / -34% / 0.80 |
| NASDAQ-100 pullback | 12% / -21% / 0.62 | 12% / -30% / 0.63 | 11% / -25% / 0.63 | 11% / -16% / 0.74 | QQQ 19% / -35% / 0.85 |

The 2026 figures cover January to 3 November only.

## What it means

1. **Only one result survives: S&P 500 momentum with an expanding window.**
   - **The choice barely changed:** from 2018 on, the 2015–2017 data already
     picked "3-month return, top 10, monthly, only the 50 most traded members".
     It kept that every year; the trend filter was on in 2018–2019, off after.
   - **So it is a true out-of-sample test of that setting:** about 26% a year
     against SPY's 14%, with a shallower worst drop (-30% vs -34%). It beat SPY
     in 8 of 9 years; the one loss was 2019, 27% vs 31%.
2. **But it is fragile to how you choose.** Re-choosing on only the last 3
   years jumped between 6 settings and fell to 19% a year with a Sharpe of 0.72,
   below SPY's 0.80. The expanding window happened to lock in a good setting
   early. A different, equally reasonable method didn't.
3. **The edge sits in one corner of the grid.** Over 2018–2026, among the 50
   most traded members, Sharpe by lookback and number of positions (monthly, no
   trend filter):

   | Lookback | top 5 | top 10 | top 20 |
   |---|---|---|---|
   | 3 months | 0.87 | 0.94 | 0.87 |
   | 6 months | 0.78 | 0.82 | 0.78 |
   | 12 months | 0.69 | 0.72 | 0.73 |

   It is stable across the number of positions but fades with longer
   lookbacks. Only 12% of all 72 settings beat SPY's 0.80. What's working is
   short-term momentum among mega-caps over a period mega-caps led. That could
   be a real effect or this era's story; nine years can't tell them apart.
4. **NASDAQ-100 momentum has no edge.** Walk-forward 17% vs QQQ's 19%, with a
   deeper drawdown (-53% vs -35%). Not choosing at all (the grid average, 21%)
   did better than choosing.
5. **Pullback does not survive.** On the S&P 500 the expanding walk-forward
   made 10% a year against SPY's 14%. The "steadiest" result from the
   single-split tuning (14.4% out-of-sample) came from one favourable split. On
   NASDAQ-100 it made 12% vs 19%. Its drawdowns are smaller than the index's,
   but so are its returns. Sharpe is no better than buy and hold.
6. **Choosing is worth little in general.** In 6 of the 8 runs, the walk-forward
   beat the grid median in at most 6 of 9 years, and the grid average often
   matched or beat the walk-forward. Most of the apparent gain from tuning in
   the single split was selection luck.

## Caveats that still apply

- **US survivorship:** Yahoo has no prices for about a quarter of 2015's S&P
  500 members (Phase 1). The "50 most traded" variant is the least affected,
  since the missing companies are mostly mid-sized ones that were acquired.
- **Unmodelled switching costs:** the fast evaluator ignores switching
  costs, but the engine replay includes them and agrees.
- **Small sample:** 9 test years, one of them partial. One bad year can move
  these numbers a lot.

## Recommendation

- **Carry forward exactly one candidate**, S&P 500 momentum with fixed
  settings: 3-month return, top 10, monthly rebalance, only the 50 most traded
  members, no trend filter. Treat it as unproven. Its evidence is one
  favourable window choice plus a plateau across position counts.
- **Paper-trade it next to SPY** rather than tuning further. Each new month is
  data that no backtest choice has seen.
- **Drop pullback** as a primary strategy, and keep **NASDAQ-100, Nifty and
  crypto** for comparison only until there is something new to test there.

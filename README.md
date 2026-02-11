English | [简体中文](docs/README_CN.md)

# Stock Scanner

A CLI tool for scanning US stocks to find investment opportunities using pluggable algorithm-based scanners. Data sourced from Yahoo Finance, cached locally as Parquet files.

## Setup

```bash
brew install uv
uv sync
```

## Quick Start

```bash
# 1. Build the ticker universe (US stocks with market cap > $5B)
uv run python main.py refresh-tickers

# 2. Fetch OHLCV + fundamentals data
uv run python main.py fetch-data

# 3. Run a scanner (auto-updates data + backtests top results)
uv run python main.py scan -s entry_point --top 20
```

## Commands

### `refresh-tickers`

Fetches all US equities (NYSE + NASDAQ) using the Yahoo Finance screener. Results cached to `data/tickers.parquet`.

```bash
uv run python main.py refresh-tickers              # Default: market cap > $5B
uv run python main.py refresh-tickers --cap 10     # Market cap > $10B
uv run python main.py refresh-tickers --cap 0      # All tickers, no filter
```

### `fetch-data`

Fetches daily OHLCV price history and fundamental data for all tickers in the universe.

```bash
uv run python main.py fetch-data                    # Fetch all (default 5 years of history)
uv run python main.py fetch-data --years 3          # 3 years of history
uv run python main.py fetch-data --full             # Force full re-download
uv run python main.py fetch-data -t AAPL -t MSFT    # Specific tickers only
uv run python main.py fetch-data --ohlcv-only       # Skip fundamentals
uv run python main.py fetch-data --fundamentals-only
```

**Caching**: OHLCV data is cached per-ticker as Parquet files. Subsequent runs only fetch new data since the last cached date. The cache is aware of trading days -- it won't re-fetch on weekends or before market close if data is already current.

### `scan`

Runs a scanner against cached data. By default, updates OHLCV data before scanning (skips automatically if cache is fresh).

```bash
uv run python main.py scan -s entry_point                      # Run scanner (auto-updates data, backtests top results)
uv run python main.py scan -s entry_point --no-update           # Skip data update
uv run python main.py scan -s entry_point --top 20              # Show top 20 results
uv run python main.py scan -s entry_point --csv                 # Export results to CSV
uv run python main.py scan -s entry_point -t AAPL -t MSFT       # Scan specific tickers
uv run python main.py scan -s ma_pullback -p pullback_pct=3     # Override scanner parameters
```

The `scan` command automatically backtests the top results after scanning. The final score is a blend of 60% scan score + 40% backtest score. The `bt` column in results shows `win_rate%/avg_return/sample_size`.

### `list-scan`

Lists all available scanners.

```bash
uv run python main.py list-scan
```

### `backtest`

Runs MA sensitivity backtesting on specific tickers or on the top results from a scanner. Walks through historical OHLCV data to find all MA touch events where the trend was aligned, then measures bounce success.

```bash
uv run python main.py backtest -t AAPL -t MSFT               # Backtest specific tickers
uv run python main.py backtest -s entry_point                  # Run scanner first, backtest top results
uv run python main.py backtest -s entry_point --top 20         # Backtest top 20 scan results
uv run python main.py backtest -t AAPL --hold-days 10          # Custom hold period (default 5)
uv run python main.py backtest -t AAPL --strategy max_return   # Use max return strategy
uv run python main.py backtest -t AAPL --csv                   # Export results to CSV
```

**Strategies:**
- `bounce` (default) -- Return % from touch-day close to close after N hold days
- `max_return` -- Best possible return (high watermark) within the hold window

**Output columns:** `win%`, `avg%`, `n` (sample size), per-MA breakdowns (`m10w%`, `m10n`, `m20w%`, `m20n`)

**Scoring:** Weighted combination of win rate and average return, with a confidence penalty for fewer than 10 historical touches.

## Scanners

All scanners use **MA5 > MA10 > MA20** as the core daily trend filter. MA50 alignment (MA20 > MA50) is optional and adds a **+15 bonus** to the score.

| Scanner | Core filter | MA50 bonus | Touch/pullback targets |
|---|---|---|---|
| `entry_point` | MA5 > MA10 > MA20 | +15 if MA20 > MA50 | MA10/MA20 |
| `strong_pullback` | MA5 > MA10 > MA20 | +15 if MA20 > MA50 | MA10/MA20 |
| `ma_pullback` | MA5 > MA10 > MA20 | +15 if MA20 > MA50 | MA5 (short) |

### `entry_point` -- Trend Entry Scanner

Finds stocks in a short-term uptrend that are at an actionable entry point near daily MA10/MA20 support.

**Filters:**
- Daily MA5 > MA10 > MA20 (short-term trend intact)
- Weekly close > weekly MA20 (intermediate uptrend)

**Entry signals** (checked over the last 3 candles):
- **HAMMER** -- Long lower wick tested MA10/MA20 and got rejected (reversed T / dragonfly doji). Strongest signal.
- **TOUCH** -- Candle low reached MA10/MA20, close held above.
- **APPROACHING** -- Price drifting toward MA10/MA20 support.

**Scoring bonuses:**
- MA50 alignment: MA20 > MA50 adds +15 points
- Recency: today's signal (ago=0) scores full points; older signals decay (0.7x, 0.4x)
- Near ATH: stocks within 3% of all-time high (no overhead resistance) get up to +25 bonus points
- Weekly alignment, daily MA spread, green candle

**Parameters:** `d_xfast`, `d_fast`, `d_mid`, `d_slow`, `w_fast`, `w_mid`, `approach_pct`, `touch_pct`, `lookback`, `wick_body_ratio`, `upper_wick_max`

### `strong_pullback` -- Strong Weekly Trend + Daily Bounce

Finds stocks with a strong weekly trend (weekly close > wMA10 > wMA20 > wMA40) that have pulled back to daily MA10/MA20 and bounced with a green candle. Daily trend requires MA5 > MA10 > MA20, with +15 bonus when MA20 > MA50.

**Parameters:** `d_xfast`, `d_fast`, `d_mid`, `d_slow`, `w_fast`, `w_mid`, `w_slow`, `lookback_days`, `touch_pct`, `min_align_days`

### `ma_pullback` -- MA Alignment + Pullback

Finds stocks where daily 5/10/20 SMAs are aligned bullishly and price has pulled back within 2% of the 5 SMA. MA20 > MA50 alignment adds +15 bonus.

**Parameters:** `ma_short`, `ma_medium`, `ma_long`, `ma_trend`, `pullback_pct`, `min_trend_days`

## Adding a New Scanner

Create a file in `scanners/` -- it's auto-discovered, no other files need changes.

```python
# scanners/my_scanner.py
from typing import Optional
import pandas as pd
from scanners.base import BaseScanner, ScanResult, resample_ohlcv
from scanners.registry import register

@register
class MyScanner(BaseScanner):
    name = "my_scanner"
    description = "Short description shown in list-scan"

    def scan(self, ticker: str, ohlcv: pd.DataFrame, fundamentals: pd.Series) -> Optional[ScanResult]:
        # ohlcv: daily OHLCV with DatetimeIndex [Open, High, Low, Close, Volume]
        # Use resample_ohlcv(ohlcv, 'W') for weekly, 'ME' for monthly

        close = ohlcv["Close"]
        # ... your logic ...

        return ScanResult(
            ticker=ticker,
            score=75.0,         # 0-100
            signal="BUY",       # STRONG_BUY / BUY / WATCH
            details={"close": round(close.iloc[-1], 2)},
        )
```

Then run: `uv run python main.py scan -s my_scanner`

## Project Structure

```
main.py                 CLI entry point
config.py               Paths and constants
pyproject.toml          Dependencies and project metadata (managed by uv)
uv.lock                 Locked dependency versions
tickers/
  universe.py           Ticker universe fetch via yfinance screener
data/
  ohlcv_cache.py        Per-ticker Parquet cache with incremental fetch
  fundamentals_cache.py Fundamentals cache (single Parquet, daily refresh)
scanners/
  base.py               BaseScanner ABC, ScanResult, resample_ohlcv helper
  registry.py           Auto-discovery via @register decorator
  ma_pullback.py        MA alignment + pullback scanner
  strong_pullback.py    Strong weekly trend + daily bounce scanner
  entry_point.py        Trend entry point scanner (touch/hammer at MA)
backtest/
  ma_sensitivity.py     MA touch backtest engine (bounce + max_return strategies)
output/
  formatter.py          Rich console table + CSV export
```

## Data Storage

All data is cached locally under `data/`:

- `data/tickers.parquet` -- Ticker universe
- `data/ohlcv/{TICKER}.parquet` -- Daily OHLCV per ticker
- `data/fundamentals.parquet` -- Fundamentals for all tickers
- `output_results/` -- CSV exports from `--csv` flag

import logging
import time

import pandas as pd
import yfinance as yf
from yfinance import EquityQuery

from config import EXCHANGES, MIN_MARKET_CAP, TICKERS_PATH

logger = logging.getLogger(__name__)

SCREEN_PAGE_SIZE = 250


def fetch_universe(min_market_cap: int = MIN_MARKET_CAP) -> pd.DataFrame:
    """
    Use yfinance screener to get all US equities on NYSE + NASDAQ,
    optionally filtered by market cap. Paginates through results.

    Args:
        min_market_cap: Minimum market cap in dollars. 0 = no filter.

    Returns:
        DataFrame with columns: symbol, shortName, exchange, marketCap,
        sector, industry (and other fields Yahoo returns).
    """
    filters = [EquityQuery("is-in", ["exchange"] + EXCHANGES)]
    if min_market_cap > 0:
        filters.append(EquityQuery("gt", ["intradaymarketcap", min_market_cap]))

    query = EquityQuery("and", filters)

    all_quotes = []
    offset = 0

    while True:
        logger.info(f"Fetching tickers offset={offset}...")
        response = yf.screen(
            query, offset=offset, size=SCREEN_PAGE_SIZE, sortField="ticker", sortAsc=True
        )
        quotes = response.get("quotes", [])
        if not quotes:
            break
        all_quotes.extend(quotes)
        offset += len(quotes)
        if len(quotes) < SCREEN_PAGE_SIZE:
            break
        time.sleep(0.3)

    df = pd.DataFrame(all_quotes)

    # Ensure key columns exist
    keep_cols = [
        "symbol", "shortName", "exchange", "marketCap",
        "sector", "industry",
    ]
    available = [c for c in keep_cols if c in df.columns]
    df = df[available]

    TICKERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(TICKERS_PATH, index=False)
    logger.info(f"Saved {len(df)} tickers to {TICKERS_PATH}")
    return df


def load_universe() -> pd.DataFrame:
    """Load cached ticker universe from Parquet."""
    if not TICKERS_PATH.exists():
        raise FileNotFoundError(
            f"Ticker universe not found at {TICKERS_PATH}. "
            "Run 'refresh-tickers' first."
        )
    return pd.read_parquet(TICKERS_PATH)

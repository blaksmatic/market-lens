from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
FUNDAMENTALS_PATH = DATA_DIR / "fundamentals.parquet"
TICKERS_PATH = DATA_DIR / "tickers.parquet"
RESULTS_DIR = PROJECT_ROOT / "results"
RESULTS_ANALYZE_DIR = RESULTS_DIR / "analyze"
RESULTS_SIMULATION_DIR = RESULTS_DIR / "simulation"
RESULTS_PORTFOLIO_DIR = RESULTS_DIR / "portfolio"

# Universe filters
MIN_MARKET_CAP = 5_000_000_000  # $5B
EXCHANGES = ["NMS", "NYQ"]  # NASDAQ Global Select + NYSE

# Data fetching
OHLCV_HISTORY_YEARS = 5
FETCH_BATCH_SIZE = 50
FETCH_SLEEP_SECONDS = 0.5
FETCH_WORKERS = 6

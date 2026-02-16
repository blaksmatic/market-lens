from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Result of a scanner evaluating a single ticker."""

    ticker: str
    score: float
    signal: str  # e.g. "BUY", "WATCH", "STRONG_BUY"
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        self.score = max(0.0, min(100.0, self.score))


@dataclass
class EntrySignal:
    """Signal to enter a position at a specific date."""

    date: pd.Timestamp
    price: float
    reason: str  # e.g. "HAMMER@MA10", "BUY", "STRONG_BUY"
    metadata: dict = field(default_factory=dict)


@dataclass
class ExitSignal:
    """Signal to exit a position at a specific date."""

    date: pd.Timestamp
    price: float
    reason: str  # e.g. "STOP_LOSS", "MA_BREAKDOWN", "PROFIT_TARGET"
    metadata: dict = field(default_factory=dict)


@dataclass
class Trade:
    """Record of a completed trade."""

    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    entry_reason: str
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    return_pct: float
    hold_days: int

    @property
    def is_win(self) -> bool:
        return self.return_pct > 0


@dataclass
class SimulationResult:
    """Complete simulation results for a single ticker."""

    ticker: str
    trades: list[Trade]
    equity_curve: pd.DataFrame  # Columns: [equity, position_value, cash]

    total_return_pct: float
    win_rate: float
    avg_return_pct: float
    max_drawdown_pct: float
    num_trades: int
    avg_hold_days: float
    total_days: int
    exit_breakdown: dict = field(default_factory=dict)  # {reason: count}


# ---------------------------------------------------------------------------
# Base scanner
# ---------------------------------------------------------------------------

class BaseScanner(ABC):
    """Base class for all scanners."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in CLI --scanner flag."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for help text."""
        ...

    @abstractmethod
    def scan(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
    ) -> Optional[ScanResult]:
        """
        Evaluate a single ticker.

        Args:
            ticker: The stock symbol.
            ohlcv: DataFrame with columns [Open, High, Low, Close, Volume],
                   DatetimeIndex, sorted ascending. Always daily frequency.
            fundamentals: Series with fundamental data for this ticker.

        Returns:
            ScanResult if the ticker passes the scan, None otherwise.
        """
        ...

    def configure(self, **kwargs) -> None:
        """Accept runtime parameters from CLI --param key=value flags."""
        pass

    # --- Simulation support ---

    def prepare_simulation(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
    ) -> None:
        """Called once per ticker before simulation loop. Override to precompute."""
        pass

    def check_entry_signal(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
        as_of_date: pd.Timestamp,
    ) -> Optional[EntrySignal]:
        """
        Check if entry conditions are met as of a specific historical date.

        Default: truncates OHLCV to as_of_date, calls scan(), wraps result.
        Override for custom entry logic.
        """
        ohlcv_slice = ohlcv.loc[:as_of_date]
        if len(ohlcv_slice) < 50:
            return None

        result = self.scan(ticker, ohlcv_slice, fundamentals)
        if result is None:
            return None

        return EntrySignal(
            date=as_of_date,
            price=ohlcv_slice["Close"].iloc[-1],
            reason=result.signal,
            metadata=result.details,
        )

    def check_exit_signal(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        entry_signal: EntrySignal,
        current_date: pd.Timestamp,
    ) -> Optional[ExitSignal]:
        """
        Check if exit conditions are met for an open position.

        Default: 10% stop-loss, 30-day time exit.
        Override in subclasses for analyzer-specific exit logic.
        """
        ohlcv_slice = ohlcv.loc[:current_date]
        if ohlcv_slice.empty:
            return None

        current_price = ohlcv_slice["Close"].iloc[-1]
        entry_price = entry_signal.price

        # Stop-loss: 10%
        if current_price < entry_price * 0.90:
            return ExitSignal(
                date=current_date,
                price=current_price,
                reason="STOP_LOSS",
            )

        # Time exit: 30 days
        days_held = (current_date - entry_signal.date).days
        if days_held >= 30:
            return ExitSignal(
                date=current_date,
                price=current_price,
                reason="TIME_EXIT",
            )

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resample_ohlcv(daily_df: pd.DataFrame, freq: str = "W") -> pd.DataFrame:
    """
    Resample daily OHLCV to a lower frequency.

    Args:
        daily_df: Daily OHLCV DataFrame with DatetimeIndex.
        freq: Pandas frequency string. Common values:
              'W'  - weekly
              'ME' - month-end
              'QE' - quarter-end

    Returns:
        Resampled OHLCV DataFrame.
    """
    return (
        daily_df.resample(freq)
        .agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        .dropna()
    )

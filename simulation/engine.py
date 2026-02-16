import logging
from typing import Optional

import pandas as pd

from scanners.base import (
    BaseScanner,
    EntrySignal,
    SimulationResult,
    Trade,
)

logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    Day-by-day trading simulator.

    Walks through each trading day in the date range:
    - If no position: check analyzer for entry signal
    - If in position: check analyzer for exit signal
    - Track portfolio value over time
    """

    def __init__(
        self,
        analyzer: BaseScanner,
        initial_capital: float = 100_000.0,
        position_size: float = 1.0,
    ):
        self.analyzer = analyzer
        self.initial_capital = initial_capital
        self.position_size = position_size

    def simulate_ticker(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> SimulationResult:
        if end_date is None:
            end_date = ohlcv.index[-1]
        if start_date is None:
            one_year_ago = end_date - pd.DateOffset(years=1)
            start_idx = ohlcv.index.searchsorted(one_year_ago)
            start_date = ohlcv.index[max(50, start_idx)]

        ohlcv_sim = ohlcv.loc[start_date:end_date]

        self.analyzer.prepare_simulation(ticker, ohlcv, fundamentals)

        trades: list[Trade] = []
        position: Optional[EntrySignal] = None
        shares = 0.0
        cash = self.initial_capital
        equity_rows: list[dict] = []

        for current_date in ohlcv_sim.index:
            current_price = ohlcv_sim.loc[current_date, "Close"]

            if position is None:
                # Check for entry
                entry = self.analyzer.check_entry_signal(
                    ticker, ohlcv, fundamentals, current_date
                )
                if entry is not None:
                    shares = (cash * self.position_size) / entry.price
                    cash -= shares * entry.price
                    position = entry
                    position_value = shares * current_price
                else:
                    position_value = 0.0
            else:
                position_value = shares * current_price

                # Check for exit
                exit_signal = self.analyzer.check_exit_signal(
                    ticker, ohlcv, position, current_date
                )
                if exit_signal is not None:
                    cash += shares * exit_signal.price
                    return_pct = (
                        (exit_signal.price - position.price) / position.price * 100
                    )
                    hold_days = (exit_signal.date - position.date).days

                    trades.append(
                        Trade(
                            ticker=ticker,
                            entry_date=position.date,
                            entry_price=position.price,
                            entry_reason=position.reason,
                            exit_date=exit_signal.date,
                            exit_price=exit_signal.price,
                            exit_reason=exit_signal.reason,
                            return_pct=round(return_pct, 2),
                            hold_days=hold_days,
                        )
                    )
                    position = None
                    shares = 0.0
                    position_value = 0.0

            equity_rows.append(
                {
                    "date": current_date,
                    "equity": cash + position_value,
                    "position_value": position_value,
                    "cash": cash,
                }
            )

        # Force-close any open position at end
        if position is not None:
            last_price = ohlcv_sim["Close"].iloc[-1]
            last_date = ohlcv_sim.index[-1]
            cash += shares * last_price
            return_pct = (last_price - position.price) / position.price * 100
            hold_days = (last_date - position.date).days
            trades.append(
                Trade(
                    ticker=ticker,
                    entry_date=position.date,
                    entry_price=position.price,
                    entry_reason=position.reason,
                    exit_date=last_date,
                    exit_price=last_price,
                    exit_reason="END_OF_DATA",
                    return_pct=round(return_pct, 2),
                    hold_days=hold_days,
                )
            )
            # Update last equity row
            if equity_rows:
                equity_rows[-1]["equity"] = cash
                equity_rows[-1]["position_value"] = 0.0
                equity_rows[-1]["cash"] = cash

        equity_curve = pd.DataFrame(equity_rows).set_index("date")

        return self._build_result(ticker, trades, equity_curve, len(ohlcv_sim))

    @staticmethod
    def _build_result(
        ticker: str,
        trades: list[Trade],
        equity_curve: pd.DataFrame,
        total_days: int,
    ) -> SimulationResult:
        if not trades:
            return SimulationResult(
                ticker=ticker,
                trades=[],
                equity_curve=equity_curve,
                total_return_pct=0.0,
                win_rate=0.0,
                avg_return_pct=0.0,
                max_drawdown_pct=0.0,
                num_trades=0,
                avg_hold_days=0.0,
                total_days=total_days,
            )

        wins = sum(1 for t in trades if t.is_win)
        win_rate = wins / len(trades) * 100
        avg_return = sum(t.return_pct for t in trades) / len(trades)
        avg_hold = sum(t.hold_days for t in trades) / len(trades)

        exit_breakdown: dict[str, int] = {}
        for t in trades:
            exit_breakdown[t.exit_reason] = exit_breakdown.get(t.exit_reason, 0) + 1

        # Max drawdown
        equity = equity_curve["equity"]
        running_max = equity.expanding().max()
        drawdown = (equity - running_max) / running_max * 100
        max_dd = drawdown.min()

        initial = equity.iloc[0] if not equity.empty else 1.0
        final = equity.iloc[-1] if not equity.empty else 1.0
        total_return = (final - initial) / initial * 100

        return SimulationResult(
            ticker=ticker,
            trades=trades,
            equity_curve=equity_curve,
            total_return_pct=round(total_return, 2),
            win_rate=round(win_rate, 1),
            avg_return_pct=round(avg_return, 2),
            max_drawdown_pct=round(max_dd, 2),
            num_trades=len(trades),
            avg_hold_days=round(avg_hold, 1),
            total_days=total_days,
            exit_breakdown=exit_breakdown,
        )

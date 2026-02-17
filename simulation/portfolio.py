import logging
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from scanners.base import BaseScanner, EntrySignal, Trade

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    ticker: str
    entry_signal: EntrySignal
    shares: float
    cost_basis: float


@dataclass
class PortfolioResult:
    trades: list[Trade]
    equity_curve: pd.DataFrame  # columns: equity, cash, positions_value, num_positions

    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    avg_return_per_trade_pct: float
    num_trades: int
    avg_hold_days: float
    total_days: int

    exit_breakdown: dict[str, int] = field(default_factory=dict)
    ticker_breakdown: dict[str, dict] = field(default_factory=dict)

    max_positions: int = 0
    position_size_pct: float = 0.0
    scanner_name: str = ""
    start_date: Optional[pd.Timestamp] = None
    end_date: Optional[pd.Timestamp] = None


class PortfolioEngine:
    """
    Portfolio-level trading simulation across multiple tickers with shared capital.

    Walks through each trading day, checks exits for open positions first,
    then checks entries across all tickers, prioritizing by scanner score.
    """

    def __init__(
        self,
        scanner: BaseScanner,
        initial_capital: float = 100_000.0,
        max_positions: int = 10,
        position_size: float = 0.10,
    ):
        self.scanner = scanner
        self.initial_capital = initial_capital
        self.max_positions = max_positions
        self.position_size = position_size

    def simulate(
        self,
        tickers: list[str],
        ohlcv_data: dict[str, pd.DataFrame],
        fundamentals_data: dict[str, pd.Series],
        start_date: Optional[pd.Timestamp] = None,
        end_date: Optional[pd.Timestamp] = None,
    ) -> PortfolioResult:
        # --- Phase 1: Precompute indicators per ticker ---
        scanner_per_ticker: dict[str, BaseScanner] = {}
        valid_tickers: list[str] = []

        for ticker in tickers:
            if ticker not in ohlcv_data or ohlcv_data[ticker].empty:
                continue
            try:
                scanner_copy = deepcopy(self.scanner)
                scanner_copy.prepare_simulation(
                    ticker,
                    ohlcv_data[ticker],
                    fundamentals_data.get(ticker, pd.Series()),
                )
                scanner_per_ticker[ticker] = scanner_copy
                valid_tickers.append(ticker)
            except Exception as e:
                logger.warning(f"Failed to prepare {ticker}: {e}")

        if not valid_tickers:
            return self._empty_result(start_date, end_date)

        # --- Phase 2: Build unified date index ---
        all_dates = sorted(
            set().union(*(ohlcv_data[t].index for t in valid_tickers))
        )
        all_dates = pd.DatetimeIndex(all_dates).sort_values()

        if end_date is None:
            end_date = all_dates[-1]
        if start_date is None:
            start_date = end_date - pd.DateOffset(years=1)

        sim_dates = all_dates[(all_dates >= start_date) & (all_dates <= end_date)]
        if len(sim_dates) == 0:
            return self._empty_result(start_date, end_date)

        # --- Phase 3: Day-by-day loop ---
        cash = self.initial_capital
        position_dollar_size = self.initial_capital * self.position_size
        open_positions: dict[str, OpenPosition] = {}
        trades: list[Trade] = []
        equity_rows: list[dict] = []

        for current_date in sim_dates:

            # Step A: Check EXITS for all open positions
            tickers_to_close = []
            for ticker, pos in open_positions.items():
                ohlcv = ohlcv_data[ticker]
                if current_date not in ohlcv.index:
                    continue

                exit_signal = scanner_per_ticker[ticker].check_exit_signal(
                    ticker, ohlcv, pos.entry_signal, current_date
                )
                if exit_signal is not None:
                    proceeds = pos.shares * exit_signal.price
                    cash += proceeds
                    return_pct = (
                        (exit_signal.price - pos.entry_signal.price)
                        / pos.entry_signal.price
                        * 100
                    )
                    hold_days = (exit_signal.date - pos.entry_signal.date).days
                    trades.append(
                        Trade(
                            ticker=ticker,
                            entry_date=pos.entry_signal.date,
                            entry_price=pos.entry_signal.price,
                            entry_reason=pos.entry_signal.reason,
                            exit_date=exit_signal.date,
                            exit_price=exit_signal.price,
                            exit_reason=exit_signal.reason,
                            return_pct=round(return_pct, 2),
                            hold_days=hold_days,
                        )
                    )
                    tickers_to_close.append(ticker)

            for ticker in tickers_to_close:
                del open_positions[ticker]

            # Step B: Check ENTRIES
            available_slots = self.max_positions - len(open_positions)

            if available_slots > 0 and cash >= position_dollar_size * 0.5:
                candidates: list[tuple[float, str, EntrySignal]] = []

                for ticker in valid_tickers:
                    if ticker in open_positions:
                        continue
                    ohlcv = ohlcv_data[ticker]
                    if current_date not in ohlcv.index:
                        continue

                    entry = scanner_per_ticker[ticker].check_entry_signal(
                        ticker,
                        ohlcv,
                        fundamentals_data.get(ticker, pd.Series()),
                        current_date,
                    )
                    if entry is not None:
                        score = entry.metadata.get("score", 0)
                        candidates.append((score, ticker, entry))

                candidates.sort(key=lambda x: x[0], reverse=True)

                for score, ticker, entry in candidates[:available_slots]:
                    dollars_to_invest = min(position_dollar_size, cash)
                    if dollars_to_invest < position_dollar_size * 0.5:
                        break

                    shares = dollars_to_invest / entry.price
                    cash -= shares * entry.price
                    open_positions[ticker] = OpenPosition(
                        ticker=ticker,
                        entry_signal=entry,
                        shares=shares,
                        cost_basis=shares * entry.price,
                    )

            # Step C: Record daily equity
            positions_value = 0.0
            for ticker, pos in open_positions.items():
                ohlcv = ohlcv_data[ticker]
                if current_date in ohlcv.index:
                    price = ohlcv.loc[current_date, "Close"]
                else:
                    prior = ohlcv.index[ohlcv.index < current_date]
                    price = (
                        ohlcv.loc[prior[-1], "Close"]
                        if len(prior) > 0
                        else pos.entry_signal.price
                    )
                positions_value += pos.shares * price

            equity_rows.append(
                {
                    "date": current_date,
                    "equity": cash + positions_value,
                    "cash": cash,
                    "positions_value": positions_value,
                    "num_positions": len(open_positions),
                }
            )

        # --- Phase 4: Force-close open positions ---
        last_date = sim_dates[-1]
        for ticker, pos in list(open_positions.items()):
            ohlcv = ohlcv_data[ticker]
            prior = ohlcv.index[ohlcv.index <= last_date]
            last_price = (
                ohlcv.loc[prior[-1], "Close"]
                if len(prior) > 0
                else pos.entry_signal.price
            )
            cash += pos.shares * last_price
            return_pct = (
                (last_price - pos.entry_signal.price)
                / pos.entry_signal.price
                * 100
            )
            hold_days = (last_date - pos.entry_signal.date).days
            trades.append(
                Trade(
                    ticker=ticker,
                    entry_date=pos.entry_signal.date,
                    entry_price=pos.entry_signal.price,
                    entry_reason=pos.entry_signal.reason,
                    exit_date=last_date,
                    exit_price=last_price,
                    exit_reason="END_OF_DATA",
                    return_pct=round(return_pct, 2),
                    hold_days=hold_days,
                )
            )

        if equity_rows:
            equity_rows[-1]["equity"] = cash
            equity_rows[-1]["cash"] = cash
            equity_rows[-1]["positions_value"] = 0.0
            equity_rows[-1]["num_positions"] = 0

        # --- Phase 5: Build result ---
        equity_curve = pd.DataFrame(equity_rows).set_index("date")
        return self._build_result(
            trades, equity_curve, sim_dates, start_date, end_date
        )

    def _build_result(
        self,
        trades: list[Trade],
        equity_curve: pd.DataFrame,
        sim_dates: pd.DatetimeIndex,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> PortfolioResult:
        initial = equity_curve["equity"].iloc[0] if not equity_curve.empty else self.initial_capital
        final = equity_curve["equity"].iloc[-1] if not equity_curve.empty else self.initial_capital
        total_return_pct = (final - initial) / initial * 100

        total_days_elapsed = (sim_dates[-1] - sim_dates[0]).days if len(sim_dates) > 1 else 0
        years = total_days_elapsed / 365.25
        if years > 0 and final > 0 and initial > 0:
            cagr = ((final / initial) ** (1 / years) - 1) * 100
        else:
            cagr = 0.0

        equity = equity_curve["equity"]
        running_max = equity.expanding().max()
        drawdown = (equity - running_max) / running_max * 100
        max_dd = drawdown.min() if not drawdown.empty else 0.0

        if trades:
            wins = sum(1 for t in trades if t.is_win)
            win_rate = wins / len(trades) * 100
            avg_return = sum(t.return_pct for t in trades) / len(trades)
            avg_hold = sum(t.hold_days for t in trades) / len(trades)
        else:
            win_rate = avg_return = avg_hold = 0.0

        exit_breakdown: dict[str, int] = {}
        for t in trades:
            exit_breakdown[t.exit_reason] = exit_breakdown.get(t.exit_reason, 0) + 1

        ticker_breakdown: dict[str, dict] = {}
        by_ticker: dict[str, list[Trade]] = defaultdict(list)
        for t in trades:
            by_ticker[t.ticker].append(t)
        for tkr, tkr_trades in by_ticker.items():
            tkr_wins = sum(1 for t in tkr_trades if t.is_win)
            tkr_avg = sum(t.return_pct for t in tkr_trades) / len(tkr_trades)
            tkr_total = sum(t.return_pct for t in tkr_trades)
            ticker_breakdown[tkr] = {
                "num_trades": len(tkr_trades),
                "win_rate": round(tkr_wins / len(tkr_trades) * 100, 1),
                "avg_return": round(tkr_avg, 2),
                "total_return": round(tkr_total, 2),
            }

        return PortfolioResult(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            final_equity=round(final, 2),
            total_return_pct=round(total_return_pct, 2),
            cagr_pct=round(cagr, 2),
            max_drawdown_pct=round(max_dd, 2),
            win_rate_pct=round(win_rate, 1),
            avg_return_per_trade_pct=round(avg_return, 2),
            num_trades=len(trades),
            avg_hold_days=round(avg_hold, 1),
            total_days=len(sim_dates),
            exit_breakdown=exit_breakdown,
            ticker_breakdown=ticker_breakdown,
            max_positions=self.max_positions,
            position_size_pct=self.position_size,
            scanner_name=self.scanner.name,
            start_date=start_date,
            end_date=end_date,
        )

    def _empty_result(
        self,
        start_date: Optional[pd.Timestamp],
        end_date: Optional[pd.Timestamp],
    ) -> PortfolioResult:
        return PortfolioResult(
            trades=[],
            equity_curve=pd.DataFrame(),
            initial_capital=self.initial_capital,
            final_equity=self.initial_capital,
            total_return_pct=0.0,
            cagr_pct=0.0,
            max_drawdown_pct=0.0,
            win_rate_pct=0.0,
            avg_return_per_trade_pct=0.0,
            num_trades=0,
            avg_hold_days=0.0,
            total_days=0,
            exit_breakdown={},
            ticker_breakdown={},
            max_positions=self.max_positions,
            position_size_pct=self.position_size,
            scanner_name=self.scanner.name,
            start_date=start_date,
            end_date=end_date,
        )

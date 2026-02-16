import csv
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scanners.base import SimulationResult


def print_simulation_results(results: list[SimulationResult], scanner_name: str):
    """Print simulation summary table and aggregate stats."""
    console = Console()

    if not results:
        console.print("[dim]No simulation results.[/dim]")
        return

    # --- Summary table ---
    table = Table(
        title=f"Simulation: {scanner_name} | {datetime.now():%Y-%m-%d %H:%M}",
        show_lines=False,
        expand=True,
    )
    table.add_column("#", style="dim", width=3, no_wrap=True)
    table.add_column("Ticker", style="bold cyan", no_wrap=True)
    table.add_column("Return %", justify="right", no_wrap=True)
    table.add_column("Win %", justify="right", no_wrap=True)
    table.add_column("Avg Ret %", justify="right", no_wrap=True)
    table.add_column("Max DD %", justify="right", no_wrap=True)
    table.add_column("Trades", justify="right", no_wrap=True)
    table.add_column("Avg Hold", justify="right", no_wrap=True)

    for i, r in enumerate(results, 1):
        ret_color = "green" if r.total_return_pct > 0 else "red"
        wr_color = "green" if r.win_rate >= 60 else ("yellow" if r.win_rate >= 50 else "white")
        table.add_row(
            str(i),
            r.ticker,
            f"[{ret_color}]{r.total_return_pct:+.1f}[/{ret_color}]",
            f"[{wr_color}]{r.win_rate:.1f}[/{wr_color}]",
            f"{r.avg_return_pct:+.2f}",
            f"{r.max_drawdown_pct:.1f}",
            str(r.num_trades),
            f"{r.avg_hold_days:.0f}d",
        )

    console.print(table)

    # --- Aggregate stats ---
    all_trades = [t for r in results for t in r.trades]
    if all_trades:
        total = len(all_trades)
        wins = sum(1 for t in all_trades if t.is_win)
        avg_ret = sum(t.return_pct for t in all_trades) / total

        exit_reasons: dict[str, int] = {}
        for t in all_trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        exit_text = ", ".join(
            f"{reason}: {count}" for reason, count in sorted(exit_reasons.items())
        )

        console.print(
            Panel(
                f"Total trades: {total}  |  Win rate: {wins / total * 100:.1f}%  |  "
                f"Avg return: {avg_ret:+.2f}%\n"
                f"Exit reasons: {exit_text}",
                title="Aggregate",
                border_style="cyan",
            )
        )

    console.print(f"\n[dim]{len(results)} tickers simulated.[/dim]")


def print_trade_log(result: SimulationResult):
    """Print detailed trade log for a single ticker."""
    console = Console()

    if not result.trades:
        console.print(f"[dim]No trades for {result.ticker}.[/dim]")
        return

    table = Table(
        title=f"Trade Log: {result.ticker}",
        show_lines=False,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Entry Date", no_wrap=True)
    table.add_column("Entry $", justify="right", no_wrap=True)
    table.add_column("Entry Reason", no_wrap=True)
    table.add_column("Exit Date", no_wrap=True)
    table.add_column("Exit $", justify="right", no_wrap=True)
    table.add_column("Exit Reason", no_wrap=True)
    table.add_column("Return %", justify="right", no_wrap=True)
    table.add_column("Days", justify="right", no_wrap=True)

    for i, t in enumerate(result.trades, 1):
        ret_color = "green" if t.is_win else "red"
        table.add_row(
            str(i),
            t.entry_date.strftime("%Y-%m-%d"),
            f"{t.entry_price:.2f}",
            t.entry_reason,
            t.exit_date.strftime("%Y-%m-%d"),
            f"{t.exit_price:.2f}",
            t.exit_reason,
            f"[{ret_color}]{t.return_pct:+.2f}[/{ret_color}]",
            str(t.hold_days),
        )

    console.print(table)


def export_simulation_csv(
    results: list[SimulationResult], scanner_name: str, output_dir: Path
) -> Path:
    """Export all trades from all tickers to a single CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / f"sim_{scanner_name}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    fieldnames = [
        "ticker",
        "entry_date",
        "entry_price",
        "entry_reason",
        "exit_date",
        "exit_price",
        "exit_reason",
        "return_pct",
        "hold_days",
    ]

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for res in results:
            for t in res.trades:
                writer.writerow(
                    {
                        "ticker": t.ticker,
                        "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                        "entry_price": f"{t.entry_price:.2f}",
                        "entry_reason": t.entry_reason,
                        "exit_date": t.exit_date.strftime("%Y-%m-%d"),
                        "exit_price": f"{t.exit_price:.2f}",
                        "exit_reason": t.exit_reason,
                        "return_pct": f"{t.return_pct:.2f}",
                        "hold_days": t.hold_days,
                    }
                )

    return filename


def export_equity_curve_csv(
    result: SimulationResult, scanner_name: str, output_dir: Path
) -> Path:
    """Export equity curve for a single ticker."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        output_dir
        / f"sim_{scanner_name}_{result.ticker}_equity_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    result.equity_curve.to_csv(filename)
    return filename

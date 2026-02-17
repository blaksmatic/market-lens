import csv
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from simulation.portfolio import PortfolioResult


def print_portfolio_summary(result: PortfolioResult):
    console = Console()

    if result.num_trades == 0:
        console.print("[dim]No trades generated.[/dim]")
        return

    ret_color = "green" if result.total_return_pct > 0 else "red"
    cagr_color = "green" if result.cagr_pct > 0 else "red"
    wr_color = "green" if result.win_rate_pct >= 60 else ("yellow" if result.win_rate_pct >= 50 else "white")

    start_str = result.start_date.strftime("%Y-%m-%d") if result.start_date else "?"
    end_str = result.end_date.strftime("%Y-%m-%d") if result.end_date else "?"

    summary = (
        f"[bold]Period:[/bold]           {start_str} to {end_str} ({result.total_days} trading days)\n"
        f"[bold]Initial Capital:[/bold]  ${result.initial_capital:,.0f}\n"
        f"[bold]Final Equity:[/bold]     ${result.final_equity:,.0f}\n"
        f"[bold]Total Return:[/bold]     [{ret_color}]{result.total_return_pct:+.2f}%[/{ret_color}]\n"
        f"[bold]CAGR:[/bold]             [{cagr_color}]{result.cagr_pct:+.2f}%[/{cagr_color}]\n"
        f"[bold]Max Drawdown:[/bold]     {result.max_drawdown_pct:.2f}%\n"
        f"\n"
        f"[bold]Trades:[/bold]           {result.num_trades}\n"
        f"[bold]Win Rate:[/bold]         [{wr_color}]{result.win_rate_pct:.1f}%[/{wr_color}]\n"
        f"[bold]Avg Return:[/bold]       {result.avg_return_per_trade_pct:+.2f}%\n"
        f"[bold]Avg Hold:[/bold]         {result.avg_hold_days:.0f} days\n"
        f"\n"
        f"[dim]Max Positions: {result.max_positions} | "
        f"Position Size: {result.position_size_pct:.0%} of capital[/dim]"
    )

    console.print(
        Panel(
            summary,
            title=f"Portfolio Simulation: {result.scanner_name}",
            border_style="cyan",
            expand=False,
        )
    )


def print_exit_breakdown(result: PortfolioResult):
    console = Console()

    if not result.exit_breakdown:
        return

    table = Table(title="Exit Reasons", show_lines=False)
    table.add_column("Reason", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("% of Total", justify="right")

    total = sum(result.exit_breakdown.values())
    for reason, count in sorted(result.exit_breakdown.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        table.add_row(reason, str(count), f"{pct:.1f}%")

    console.print(table)


def print_portfolio_trade_log(result: PortfolioResult):
    console = Console()

    if not result.trades:
        console.print("[dim]No trades.[/dim]")
        return

    sorted_trades = sorted(result.trades, key=lambda t: t.entry_date)

    table = Table(title="Trade Log", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Ticker", style="bold cyan", no_wrap=True)
    table.add_column("Entry Date", no_wrap=True)
    table.add_column("Entry $", justify="right", no_wrap=True)
    table.add_column("Exit Date", no_wrap=True)
    table.add_column("Exit $", justify="right", no_wrap=True)
    table.add_column("Exit Reason", no_wrap=True)
    table.add_column("Return %", justify="right", no_wrap=True)
    table.add_column("Days", justify="right", no_wrap=True)

    for i, t in enumerate(sorted_trades, 1):
        ret_color = "green" if t.is_win else "red"
        table.add_row(
            str(i),
            t.ticker,
            t.entry_date.strftime("%Y-%m-%d"),
            f"{t.entry_price:.2f}",
            t.exit_date.strftime("%Y-%m-%d"),
            f"{t.exit_price:.2f}",
            t.exit_reason,
            f"[{ret_color}]{t.return_pct:+.2f}[/{ret_color}]",
            str(t.hold_days),
        )

    console.print(table)


def print_ticker_breakdown(result: PortfolioResult):
    console = Console()

    if not result.ticker_breakdown:
        return

    sorted_tickers = sorted(
        result.ticker_breakdown.items(),
        key=lambda x: x[1]["total_return"],
        reverse=True,
    )

    table = Table(title="Per-Ticker Breakdown", show_lines=False)
    table.add_column("Ticker", style="bold cyan", no_wrap=True)
    table.add_column("Trades", justify="right")
    table.add_column("Win %", justify="right")
    table.add_column("Avg Ret %", justify="right")
    table.add_column("Total Ret %", justify="right")

    for ticker, stats in sorted_tickers:
        ret_color = "green" if stats["total_return"] > 0 else "red"
        wr_color = "green" if stats["win_rate"] >= 60 else ("yellow" if stats["win_rate"] >= 50 else "white")
        table.add_row(
            ticker,
            str(stats["num_trades"]),
            f"[{wr_color}]{stats['win_rate']:.1f}[/{wr_color}]",
            f"{stats['avg_return']:+.2f}",
            f"[{ret_color}]{stats['total_return']:+.2f}[/{ret_color}]",
        )

    console.print(table)


def export_portfolio_csv(result: PortfolioResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / f"portfolio_{result.scanner_name}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    fieldnames = [
        "ticker", "entry_date", "entry_price", "entry_reason",
        "exit_date", "exit_price", "exit_reason", "return_pct", "hold_days",
    ]

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in sorted(result.trades, key=lambda t: t.entry_date):
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


def export_portfolio_equity_csv(result: PortfolioResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = output_dir / f"portfolio_{result.scanner_name}_equity_{datetime.now():%Y%m%d_%H%M%S}.csv"
    result.equity_curve.to_csv(filename)
    return filename

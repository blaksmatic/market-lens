import logging
import sys

import click
import pandas as pd

from config import FUNDAMENTALS_PATH, OHLCV_DIR, OHLCV_HISTORY_YEARS, OUTPUT_DIR
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
def cli():
    """Market Lens CLI - analyze US stocks for investment opportunities."""
    pass


@cli.command("fetch-universe")
@click.option("--cap", type=float, default=5, show_default=True, help="Min market cap in billions. 0 = no filter.")
def refresh_tickers(cap):
    """Refresh the ticker universe from Yahoo Finance screener."""
    from tickers.universe import fetch_universe

    min_cap = int(cap * 1e9)
    if min_cap > 0:
        click.echo(f"Fetching US equities with market cap > ${cap:.0f}B from NYSE + NASDAQ...")
    else:
        click.echo("Fetching all US equities from NYSE + NASDAQ...")
    df = fetch_universe(min_market_cap=min_cap)
    click.echo(f"Found {len(df)} tickers.")


@cli.command("fetch-data")
@click.option("--years", type=int, default=OHLCV_HISTORY_YEARS, show_default=True, help="Years of OHLCV history to fetch.")
@click.option("--full", is_flag=True, help="Force full re-download, ignoring cache.")
@click.option("--ticker", "-t", multiple=True, help="Fetch specific ticker(s) only.")
@click.option("--fundamentals-only", is_flag=True, help="Only refresh fundamentals.")
@click.option("--ohlcv-only", is_flag=True, help="Only refresh OHLCV data.")
def fetch_data(years, full, ticker, fundamentals_only, ohlcv_only):
    """Fetch/update OHLCV and fundamentals data."""
    from data.fundamentals_cache import fetch_fundamentals
    from data.ohlcv_cache import fetch_all_ohlcv
    from tickers.universe import load_universe

    if ticker:
        tickers = list(ticker)
    else:
        tickers = load_universe()["symbol"].tolist()

    if not fundamentals_only:
        click.echo(f"Fetching OHLCV for {len(tickers)} tickers ({years}yr history)...")
        failed = fetch_all_ohlcv(tickers, years=years, force_full=full)
        if failed:
            click.echo(f"  {len(failed)} tickers failed.")

    if not ohlcv_only:
        click.echo(f"Fetching fundamentals for {len(tickers)} tickers...")
        fetch_fundamentals(tickers, use_cache=not full)

    click.echo("Done.")


@cli.command("analyze")
@click.option("--scanner", "-s", required=True, help="Analyzer name to run.")
@click.option("--csv", "export_csv", is_flag=True, help="Export results to CSV.")
@click.option("--top", type=int, default=None, help="Show only top N results.")
@click.option("--param", "-p", multiple=True, help="Scanner param as key=value.")
@click.option("--ticker", "-t", multiple=True, help="Scan specific ticker(s) instead of universe.")
@click.option("--no-update", is_flag=True, help="Skip data refresh, use cached data only.")
def scan(scanner, export_csv, top, param, ticker, no_update):
    """Run an analyzer against cached data. Updates OHLCV data first by default."""
    from scanners.registry import auto_discover, get_scanner
    from output.formatter import print_results, export_csv as do_export_csv
    from data.ohlcv_cache import fetch_all_ohlcv
    from tickers.universe import load_universe

    auto_discover()
    scanner_obj = get_scanner(scanner)

    if param:
        params = dict(p.split("=", 1) for p in param)
        scanner_obj.configure(**params)

    if ticker:
        symbols = list(ticker)
    else:
        tickers_df = load_universe()
        symbols = tickers_df["symbol"].tolist()

    if not no_update:
        click.echo(f"Updating OHLCV for {len(symbols)} tickers...")
        failed = fetch_all_ohlcv(symbols)
        if failed:
            click.echo(f"  {len(failed)} tickers failed to update.")

    fundamentals_df = None
    if FUNDAMENTALS_PATH.exists():
        fundamentals_df = pd.read_parquet(FUNDAMENTALS_PATH)

    results = []
    skipped = 0
    for sym in tqdm(symbols, desc=f"Scanning [{scanner}]"):
        ohlcv_path = OHLCV_DIR / f"{sym}.parquet"
        if not ohlcv_path.exists():
            skipped += 1
            continue

        ohlcv = pd.read_parquet(ohlcv_path)
        if fundamentals_df is not None and sym in fundamentals_df.index:
            fund = fundamentals_df.loc[sym]
        else:
            fund = pd.Series()

        result = scanner_obj.scan(sym, ohlcv, fund)
        if result is not None:
            results.append(result)

    if skipped:
        click.echo(f"  Skipped {skipped} tickers (no OHLCV cache).")

    results = sorted(results, key=lambda r: r.score, reverse=True)
    if top:
        results = results[:top]

    # Auto-backtest top results
    if results:
        from backtest.ma_sensitivity import backtest_ma_sensitivity

        bt_top = min(len(results), top or 40)
        click.echo(f"Backtesting top {bt_top} results...")
        for r in results[:bt_top]:
            ohlcv_path = OHLCV_DIR / f"{r.ticker}.parquet"
            ohlcv = pd.read_parquet(ohlcv_path)
            bt = backtest_ma_sensitivity(ohlcv)
            r.details["bt"] = f"{bt['win_rate']}%/{bt['avg_return']}/{bt['total_touches']}n"
            # Combined score: 60% scan + 40% backtest
            r.score = round(r.score * 0.6 + bt["backtest_score"] * 0.4, 1)

        results = sorted(results, key=lambda r: r.score, reverse=True)

    if results:
        print_results(results, scanner)
        if export_csv:
            path = do_export_csv(results, scanner, OUTPUT_DIR)
            click.echo(f"CSV exported to {path}")
    else:
        click.echo("No results matched the scanner criteria.")


@cli.command("list-analyzers")
def list_analyzers():
    """List all available analyzers."""
    from scanners.registry import auto_discover, list_scanners

    auto_discover()
    scanners = list_scanners()
    if not scanners:
        click.echo("No scanners found.")
        return
    for name, desc in scanners.items():
        click.echo(f"  {name:20s}  {desc}")


@cli.command("backtest")
@click.option("--ticker", "-t", multiple=True, help="Ticker(s) to backtest.")
@click.option("--scanner", "-s", default=None, help="Run scanner first, then backtest top results.")
@click.option("--top", type=int, default=40, show_default=True, help="How many scan results to backtest.")
@click.option("--hold-days", type=int, default=5, show_default=True, help="Hold period in days.")
@click.option("--strategy", type=click.Choice(["bounce", "max_return"]), default="bounce", show_default=True, help="Backtest strategy.")
@click.option("--csv", "export_csv", is_flag=True, help="Export results to CSV.")
def backtest_cmd(ticker, scanner, top, hold_days, strategy, export_csv):
    """Run MA sensitivity backtest on tickers or scan results."""
    from backtest.ma_sensitivity import backtest_ma_sensitivity, list_strategies
    from output.formatter import print_results, export_csv as do_export_csv
    from scanners.base import ScanResult

    symbols = []

    if ticker:
        symbols = list(ticker)
    elif scanner:
        from scanners.registry import auto_discover, get_scanner
        from data.ohlcv_cache import fetch_all_ohlcv
        from tickers.universe import load_universe

        auto_discover()
        scanner_obj = get_scanner(scanner)

        tickers_df = load_universe()
        all_symbols = tickers_df["symbol"].tolist()

        click.echo(f"Running scanner [{scanner}] on {len(all_symbols)} tickers...")
        fundamentals_df = None
        if FUNDAMENTALS_PATH.exists():
            fundamentals_df = pd.read_parquet(FUNDAMENTALS_PATH)

        scan_results = []
        for sym in tqdm(all_symbols, desc=f"Scanning [{scanner}]"):
            ohlcv_path = OHLCV_DIR / f"{sym}.parquet"
            if not ohlcv_path.exists():
                continue
            ohlcv = pd.read_parquet(ohlcv_path)
            fund = fundamentals_df.loc[sym] if fundamentals_df is not None and sym in fundamentals_df.index else pd.Series()
            result = scanner_obj.scan(sym, ohlcv, fund)
            if result is not None:
                scan_results.append(result)

        scan_results = sorted(scan_results, key=lambda r: r.score, reverse=True)[:top]
        symbols = [r.ticker for r in scan_results]
        click.echo(f"Top {len(symbols)} results selected for backtesting.")
    else:
        click.echo("Provide --ticker or --scanner. Use --help for details.")
        sys.exit(1)

    # Run backtest
    results = []
    for sym in tqdm(symbols, desc="Backtesting"):
        ohlcv_path = OHLCV_DIR / f"{sym}.parquet"
        if not ohlcv_path.exists():
            click.echo(f"  {sym}: no OHLCV data, skipping.")
            continue

        ohlcv = pd.read_parquet(ohlcv_path)
        bt = backtest_ma_sensitivity(
            ohlcv, hold_days=hold_days, strategy=strategy,
        )

        results.append(ScanResult(
            ticker=sym,
            score=bt["backtest_score"],
            signal="STRONG_BUY" if bt["win_rate"] >= 65 else ("BUY" if bt["win_rate"] >= 50 else "WATCH"),
            details={
                "win%": bt["win_rate"],
                "avg%": bt["avg_return"],
                "n": bt["total_touches"],
                "m10w%": bt["ma10_win_rate"],
                "m10n": bt["ma10_touches"],
                "m20w%": bt["ma20_win_rate"],
                "m20n": bt["ma20_touches"],
            },
        ))

    if results:
        print_results(results, f"backtest ({strategy}, {hold_days}d)")
        if export_csv:
            path = do_export_csv(results, f"backtest_{strategy}", OUTPUT_DIR)
            click.echo(f"CSV exported to {path}")
    else:
        click.echo("No backtest results.")


@cli.command("simulate")
@click.option("--scanner", "-s", required=True, help="Analyzer to simulate.")
@click.option("--ticker", "-t", multiple=True, help="Ticker(s) to simulate.")
@click.option("--start", type=str, default=None, help="Start date (YYYY-MM-DD).")
@click.option("--end", type=str, default=None, help="End date (YYYY-MM-DD).")
@click.option("--capital", type=float, default=100000, show_default=True, help="Initial capital.")
@click.option("--position-size", type=float, default=1.0, show_default=True, help="Fraction of capital per trade (0-1).")
@click.option("--top", type=int, default=None, help="Show only top N results by return.")
@click.option("--csv", "export_csv", is_flag=True, help="Export trade log to CSV.")
@click.option("--equity-curve", is_flag=True, help="Export equity curve CSV per ticker.")
@click.option("--no-update", is_flag=True, help="Skip data refresh.")
def simulate_cmd(scanner, ticker, start, end, capital, position_size, top, export_csv, equity_curve, no_update):
    """Run day-by-day trading simulation with analyzer-specific entry/exit logic."""
    from scanners.registry import auto_discover, get_scanner
    from simulation.engine import SimulationEngine
    from output.simulator_formatter import (
        print_simulation_results,
        print_trade_log,
        export_simulation_csv,
        export_equity_curve_csv,
    )
    from data.ohlcv_cache import fetch_all_ohlcv
    from tickers.universe import load_universe

    auto_discover()
    scanner_obj = get_scanner(scanner)

    start_date = pd.Timestamp(start) if start else None
    end_date = pd.Timestamp(end) if end else None

    if ticker:
        symbols = list(ticker)
    else:
        tickers_df = load_universe()
        symbols = tickers_df["symbol"].tolist()

    if not no_update:
        click.echo(f"Updating OHLCV for {len(symbols)} tickers...")
        failed = fetch_all_ohlcv(symbols)
        if failed:
            click.echo(f"  {len(failed)} tickers failed to update.")

    fundamentals_df = None
    if FUNDAMENTALS_PATH.exists():
        fundamentals_df = pd.read_parquet(FUNDAMENTALS_PATH)

    engine = SimulationEngine(scanner_obj, initial_capital=capital, position_size=position_size)
    results = []
    skipped = 0

    for sym in tqdm(symbols, desc=f"Simulating [{scanner}]"):
        ohlcv_path = OHLCV_DIR / f"{sym}.parquet"
        if not ohlcv_path.exists():
            skipped += 1
            continue

        ohlcv = pd.read_parquet(ohlcv_path)
        if fundamentals_df is not None and sym in fundamentals_df.index:
            fund = fundamentals_df.loc[sym]
        else:
            fund = pd.Series()

        try:
            sim_result = engine.simulate_ticker(sym, ohlcv, fund, start_date, end_date)
            if sim_result.num_trades > 0:
                results.append(sim_result)
        except Exception as e:
            logger.warning(f"Simulation failed for {sym}: {e}")

    if skipped:
        click.echo(f"  Skipped {skipped} tickers (no OHLCV cache).")

    results = sorted(results, key=lambda r: r.total_return_pct, reverse=True)
    if top:
        results = results[:top]

    if results:
        print_simulation_results(results, scanner)

        # Print trade log for single-ticker runs
        if len(results) == 1:
            print_trade_log(results[0])

        if export_csv:
            path = export_simulation_csv(results, scanner, OUTPUT_DIR)
            click.echo(f"Trade log CSV exported to {path}")

        if equity_curve:
            for res in results:
                export_equity_curve_csv(res, scanner, OUTPUT_DIR)
            click.echo(f"Equity curves exported to {OUTPUT_DIR}")
    else:
        click.echo("No simulation results (no trades generated).")


if __name__ == "__main__":
    cli()

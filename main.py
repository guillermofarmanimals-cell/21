"""
MJ APEX TRADER — CLI Entry Point
Interactive session loop, one-shot commands, and risk calculator mode.

Usage:
  python main.py                    # Open full interactive session
  python main.py --session          # Auto-run Intelligence Gathering Protocol
  python main.py --calc             # Companion risk calculator mode
  python main.py --audit            # Run session audit
  python main.py --xl               # Print XL status
  python main.py --query "..."      # Single query, then exit
"""

from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
from mt5_bridge import MT5Bridge
from risk_manager import RiskManager
from xl_tracker import XLTracker, TradingMode
from agent import MJApexTrader
from risk_calculator import RiskCalculator

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler("mj_apex.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("mj_apex.main")

console = Console()


# ── Banner ────────────────────────────────────────────────────────────────────

def print_banner(xl: XLTracker) -> None:
    status = xl.status_report()
    mode_color = {
        TradingMode.NORMAL:   "green",
        TradingMode.SURVIVAL: "yellow",
        TradingMode.PAUSED:   "red",
        TradingMode.DELETED:  "bright_red",
    }.get(status["mode"], "white")

    banner = Text()
    banner.append("  MJ APEX TRADER\n", style="bold white")
    banner.append("  Autonomous Trading Intelligence\n", style="dim white")
    banner.append(f"\n  XL: {status['xl']:,}  ", style="bold cyan")
    banner.append(f"MODE: {status['mode'].upper()}", style=f"bold {mode_color}")
    banner.append(f"\n  Trades: {status['total_trades']}  ", style="white")
    banner.append(f"Win Rate: {status['win_rate']:.1%}  ", style="green")
    banner.append(f"Consecutive Losses: {status['consecutive_losses']}", style="yellow")

    if status["is_paused"]:
        banner.append(f"\n  ⏸  PAUSED until {status['pause_until']}", style="bold red")
    if status["mode"] == TradingMode.SURVIVAL:
        banner.append("\n  ⚠  SURVIVAL MODE — 1% risk, 5/5 confluence required", style="bold yellow")
    if status["mode"] == TradingMode.DELETED:
        banner.append("\n  ✖  DELETED — No trading permitted", style="bold bright_red")

    console.print(Panel(banner, title="[bold gold1]MJ APEX TRADER[/bold gold1]",
                        border_style="gold1", box=box.DOUBLE))


def print_xl_table(xl: XLTracker) -> None:
    s = xl.status_report()
    t = Table(title="XL Status Report", box=box.ROUNDED, border_style="cyan")
    t.add_column("Metric", style="bold")
    t.add_column("Value", style="cyan")

    t.add_row("XL Score",           f"{s['xl']:,}")
    t.add_row("Mode",                s["mode"].upper())
    t.add_row("Total Trades",        str(s["total_trades"]))
    t.add_row("Wins",                str(s["total_wins"]))
    t.add_row("Losses",              str(s["total_losses"]))
    t.add_row("Win Rate",            f"{s['win_rate']:.1%}")
    t.add_row("Consecutive Losses",  str(s["consecutive_losses"]))
    t.add_row("Peak Balance",        f"${s['peak_balance']:.2f}")
    t.add_row("XL to Survival Zone", str(s["xl_to_survival"]))
    t.add_row("XL to World Bot",     f"{s['xl_to_world_bot']:,}")
    t.add_row("World Bot Achieved",  "YES 🏆" if s["world_bot_achieved"] else "No")

    console.print(t)


# ── Risk Calculator Mode ──────────────────────────────────────────────────────

def run_risk_calculator() -> None:
    console.print("\n[bold gold1]MJ APEX — COMPANION RISK CALCULATOR[/bold gold1]")
    console.print("[dim]Enter trade parameters to generate a trade card.[/dim]\n")

    calc = RiskCalculator()

    while True:
        try:
            console.print("[bold]New calculation (Ctrl-C to exit)[/bold]")
            symbol    = console.input("  Symbol (e.g. EURUSD): ").strip().upper() or "EURUSD"
            direction = console.input("  Direction (BUY/SELL): ").strip().upper() or "BUY"
            entry     = float(console.input("  Entry price: ").strip())
            sl        = float(console.input("  Stop Loss price: ").strip())
            tp        = float(console.input("  Take Profit price: ").strip())
            balance   = float(console.input("  Account balance ($): ").strip())
            xl_count  = int(console.input("  XL count (default 100): ").strip() or "100")
            xl_mode   = console.input("  XL mode (normal/survival, default normal): ").strip() or "normal"

            console.print("\n[dim]Calculating...[/dim]")
            card = calc.calculate(symbol, direction, entry, sl, tp, balance, xl_count, xl_mode)
            console.print(Panel(card, title="TRADE CARD", border_style="gold1"))

        except KeyboardInterrupt:
            console.print("\n[dim]Risk calculator closed.[/dim]")
            break
        except ValueError as e:
            console.print(f"[red]Invalid input: {e}[/red]")


# ── Interactive Session Loop ──────────────────────────────────────────────────

def run_interactive(agent: MJApexTrader, auto_session: bool = False) -> None:
    console.print("\n[dim]Type your prompt and press Enter. Commands: /quit /audit /xl /session /calc /clear[/dim]\n")

    if auto_session:
        console.print("[bold gold1]Running Intelligence Gathering Protocol...[/bold gold1]\n")
        agent.open_session()

    while True:
        try:
            raw = console.input("[bold gold1]MJ >[/bold gold1] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session ended.[/dim]")
            break

        if not raw:
            continue

        # Built-in commands
        if raw.lower() in ("/quit", "/exit", "quit", "exit"):
            console.print("[dim]MJ APEX TRADER session closed.[/dim]")
            break

        if raw.lower() == "/xl":
            print_xl_table(agent.xl)
            continue

        if raw.lower() == "/audit":
            agent.query("Run a session audit on the last 10 trades.")
            continue

        if raw.lower() == "/session":
            console.print("[bold gold1]Opening new intelligence session...[/bold gold1]\n")
            agent.open_session()
            continue

        if raw.lower() == "/calc":
            run_risk_calculator()
            continue

        if raw.lower() == "/clear":
            agent.new_session()
            console.print("[dim]Conversation history cleared.[/dim]")
            continue

        # Forward to agent
        agent.query(raw)


# ── Entry Point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MJ APEX TRADER — Autonomous MT5 Trading Agent"
    )
    parser.add_argument("--session", action="store_true",
                        help="Auto-run Intelligence Gathering Protocol on start")
    parser.add_argument("--calc",    action="store_true",
                        help="Run companion risk calculator only")
    parser.add_argument("--audit",   action="store_true",
                        help="Run session audit and exit")
    parser.add_argument("--xl",      action="store_true",
                        help="Print XL status and exit")
    parser.add_argument("--query",   type=str, default="",
                        help="Single query to run non-interactively")
    parser.add_argument("--no-stream", action="store_true",
                        help="Disable streaming (batch/non-interactive use)")
    args = parser.parse_args()

    # ── Initialise components ─────────────────────────────────────────────────
    xl   = XLTracker()
    mt5  = MT5Bridge(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
    risk = RiskManager()

    # Print banner
    print_banner(xl)

    # Short-circuit modes
    if args.xl:
        print_xl_table(xl)
        return

    if args.calc:
        run_risk_calculator()
        return

    # Connect to MT5
    console.print("[dim]Connecting to MetaTrader 5...[/dim]")
    if not mt5.connect():
        console.print("[yellow]⚠  MT5 connection failed — running in SIMULATION mode[/yellow]")
    else:
        console.print("[green]✓  MT5 connected[/green]")

    # Build agent
    agent = MJApexTrader(mt5, risk, xl, verbose=not args.no_stream)

    try:
        if args.audit:
            agent.query("Run a full session audit on the last 10 trades and provide recommendations.")

        elif args.query:
            agent.new_session()
            agent.query(args.query)

        else:
            run_interactive(agent, auto_session=args.session)

    finally:
        mt5.disconnect()
        console.print("[dim]MT5 disconnected.[/dim]")


if __name__ == "__main__":
    main()

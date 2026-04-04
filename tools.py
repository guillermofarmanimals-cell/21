"""
MJ APEX TRADER — Tool Definitions & Handlers
All 16 tools wired to their underlying modules.
"""

from __future__ import annotations
import json
import logging
from typing import Any
from datetime import datetime, timezone

from mt5_bridge import MT5Bridge
from risk_manager import RiskManager
from xl_tracker import XLTracker
from intelligence import (
    EconomicCalendar, COTAnalyzer, SentimentAnalyzer,
    CycleAnalyzer, SupplyDemandDetector, StrategyValidator,
)

logger = logging.getLogger("mj_apex.tools")


# ── Tool Schema Definitions (passed to Claude) ───────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "get_account_info",
        "description": (
            "Retrieve live MT5 account information: balance, equity, margin, "
            "open trade count, and profit/loss. Run this at the start of every session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_open_positions",
        "description": (
            "List all currently open positions with ticket number, symbol, direction, "
            "lot size, open price, SL, TP, and unrealized P&L."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_market_data",
        "description": (
            "Fetch OHLCV candlestick data for technical analysis. "
            "Use this to assess trend structure, identify key levels, "
            "and locate supply/demand zones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string", "description": "MT5 symbol, e.g. EURUSD, XAUUSD, US30"},
                "timeframe": {
                    "type": "string",
                    "enum": ["M1","M5","M15","M30","H1","H4","D1","W1"],
                    "description": "Chart timeframe",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of candles to retrieve (default 100, max 500)",
                    "default": 100,
                },
            },
            "required": ["symbol", "timeframe"],
        },
    },
    {
        "name": "get_economic_calendar",
        "description": (
            "Retrieve upcoming HIGH-IMPACT economic news events within the next N hours. "
            "Always check this before any trade entry. "
            "Events within 15 minutes trigger the news blackout rule."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hours_ahead": {
                    "type": "integer",
                    "description": "How many hours ahead to scan (default 24)",
                    "default": 24,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_cot_report",
        "description": (
            "Retrieve Commitment of Traders (COT) data showing institutional "
            "positioning (commercial vs non-commercial). Use this for C4 Smart Money Signal. "
            "Trade with institutions, never against them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Symbol to get COT data for"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_sentiment",
        "description": (
            "Get Fear & Greed index score and label. "
            "Extreme Fear may signal buying opportunities; "
            "Extreme Greed may signal reversal risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_lunar_cycle",
        "description": (
            "Get the current lunar phase, days to new/full moon, "
            "Mercury retrograde status, and Shemitah/Jubilee cycle position. "
            "Lunar windows near new/full moons qualify for C5 cycle confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_supply_demand_zones",
        "description": (
            "Detect institutional supply and demand zones for a symbol on a given timeframe. "
            "Required for C2 Institutional Zone confluence. "
            "Zones must have caused at least a 50-pip prior move to qualify."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string"},
                "timeframe": {
                    "type": "string",
                    "enum": ["H1","H4","D1"],
                    "description": "Timeframe for zone detection (H1 minimum)",
                },
                "candle_count": {
                    "type": "integer",
                    "description": "Lookback candles (default 200)",
                    "default": 200,
                },
            },
            "required": ["symbol", "timeframe"],
        },
    },
    {
        "name": "calculate_lot_size",
        "description": (
            "Calculate the correct lot size based on current account balance, "
            "XL mode (normal/survival), stop loss in pips, and take profit in pips. "
            "Enforces all risk tier rules and minimum 1:2 RR. "
            "ALWAYS call this before execute_trade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":           {"type": "string"},
                "stop_loss_pips":   {"type": "number", "description": "Distance from entry to SL in pips"},
                "take_profit_pips": {"type": "number", "description": "Distance from entry to TP in pips"},
            },
            "required": ["symbol", "stop_loss_pips", "take_profit_pips"],
        },
    },
    {
        "name": "validate_strategy",
        "description": (
            "Run the Phase 2 & 3 backtest validation protocol on a strategy's "
            "trade history. Requires minimum 100 trades. A strategy MUST pass "
            "all 5 criteria (95% win rate, 1:2+ RR, ≤3 consec losses, ≤20% drawdown) "
            "before any live deployment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trades": {
                    "type": "array",
                    "description": "Array of trade results",
                    "items": {
                        "type": "object",
                        "properties": {
                            "outcome": {"type": "string", "enum": ["win", "loss"]},
                            "rr":      {"type": "number", "description": "Actual RR achieved"},
                            "context": {"type": "string", "description": "Market context"},
                        },
                        "required": ["outcome"],
                    },
                },
            },
            "required": ["trades"],
        },
    },
    {
        "name": "execute_trade",
        "description": (
            "Place a live market order on MT5 with mandatory stop loss and take profit. "
            "ONLY call this after ALL 8 pre-trade checklist items are confirmed. "
            "Will be rejected if: confluence < 3, open trades >= 2, "
            "news blackout active, XL mode is PAUSED or DELETED."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol":           {"type": "string"},
                "direction":        {"type": "string", "enum": ["BUY", "SELL"]},
                "lots":             {"type": "number", "description": "Lot size from calculate_lot_size"},
                "stop_loss_price":  {"type": "number", "description": "Stop loss price level"},
                "take_profit_price":{"type": "number", "description": "Take profit price level"},
                "confluence_score": {
                    "type": "integer", "minimum": 0, "maximum": 5,
                    "description": "Your confluence stack score (must be ≥3, or 5 in survival mode)",
                },
                "comment": {"type": "string", "description": "Trade rationale (optional)"},
            },
            "required": ["symbol","direction","lots","stop_loss_price","take_profit_price","confluence_score"],
        },
    },
    {
        "name": "close_position",
        "description": "Close an existing open position by ticket number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket": {"type": "integer", "description": "MT5 position ticket number"},
            },
            "required": ["ticket"],
        },
    },
    {
        "name": "modify_position",
        "description": (
            "Adjust the stop loss and/or take profit of an open position. "
            "Stop loss may ONLY be moved TOWARD the entry (tightened). "
            "Moving SL further away is forbidden by hard rules."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket":      {"type": "integer"},
                "stop_loss":   {"type": "number", "description": "New SL price"},
                "take_profit": {"type": "number", "description": "New TP price"},
            },
            "required": ["ticket", "stop_loss", "take_profit"],
        },
    },
    {
        "name": "get_xl_status",
        "description": (
            "Retrieve the current XL score, trading mode (normal/survival/paused/deleted), "
            "consecutive loss count, win rate, and performance summary. "
            "Check this at session start."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "log_performance",
        "description": (
            "Record a completed trade outcome to the XL tracker and performance log. "
            "MUST be called after every trade closes. "
            "Provide the actual profit/loss and whether it was a win or loss."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket":          {"type": "integer"},
                "symbol":          {"type": "string"},
                "outcome":         {"type": "string", "enum": ["win", "loss"]},
                "profit_usd":      {"type": "number", "description": "Actual P&L in USD (negative for loss)"},
                "account_balance": {"type": "number", "description": "Account balance after trade closed"},
            },
            "required": ["ticket", "symbol", "outcome", "profit_usd", "account_balance"],
        },
    },
    {
        "name": "run_session_audit",
        "description": (
            "Run a full self-audit across the last N trades. "
            "Reviews win rate, RR achieved, XL trajectory, and flags "
            "any patterns of revenge trading or strategy drift. "
            "Required every 10 trades."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "last_n_trades": {
                    "type": "integer",
                    "description": "Number of recent trades to audit (default 10)",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
]


# ── Tool Handler Registry ─────────────────────────────────────────────────────

class ToolHandler:
    """
    Executes tool calls dispatched by the Claude agentic loop.
    Each handle_* method returns a JSON-serialisable dict.
    """

    def __init__(
        self,
        mt5: MT5Bridge,
        risk: RiskManager,
        xl: XLTracker,
    ) -> None:
        self.mt5       = mt5
        self.risk      = risk
        self.xl        = xl
        self.calendar  = EconomicCalendar()
        self.cot       = COTAnalyzer()
        self.sentiment = SentimentAnalyzer()
        self.cycles    = CycleAnalyzer()
        self.sd_detect = SupplyDemandDetector()
        self.validator = StrategyValidator()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def execute(self, tool_name: str, tool_input: dict) -> dict:
        handler = getattr(self, f"_handle_{tool_name}", None)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return handler(**tool_input)
        except Exception as exc:
            logger.exception("Tool %s raised: %s", tool_name, exc)
            return {"error": str(exc), "tool": tool_name}

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _handle_get_account_info(self) -> dict:
        return self.mt5.get_account_info()

    def _handle_get_open_positions(self) -> dict:
        positions = self.mt5.get_open_positions()
        return {"open_positions": positions, "count": len(positions)}

    def _handle_get_market_data(
        self, symbol: str, timeframe: str, count: int = 100
    ) -> dict:
        count = min(count, 500)
        candles = self.mt5.get_candles(symbol, timeframe, count)
        if candles and "error" in candles[0]:
            return candles[0]
        return {
            "symbol":    symbol,
            "timeframe": timeframe,
            "candles":   candles,
            "count":     len(candles),
        }

    def _handle_get_economic_calendar(self, hours_ahead: int = 24) -> dict:
        events = self.calendar.get_upcoming(hours_ahead)
        minutes_to_next = self.calendar.minutes_to_next_news()
        return {
            "events":                events,
            "count":                 len(events),
            "minutes_to_next_news":  minutes_to_next,
            "blackout_active":       minutes_to_next is not None and minutes_to_next < 15,
        }

    def _handle_get_cot_report(self, symbol: str) -> dict:
        return self.cot.get_positioning(symbol)

    def _handle_get_market_sentiment(self) -> dict:
        return self.sentiment.get_fear_greed()

    def _handle_get_lunar_cycle(self) -> dict:
        lunar   = self.cycles.get_lunar_phase()
        mercury = self.cycles.get_mercury_retrograde()
        jubilee = self.cycles.jubilee_cycle_check()
        return {
            "lunar":             lunar,
            "mercury_retrograde": mercury,
            "jubilee_shemitah":  jubilee,
            "c5_active": (
                lunar["c5_confirmation"] or not mercury["active"]
            ),
        }

    def _handle_check_supply_demand_zones(
        self, symbol: str, timeframe: str, candle_count: int = 200
    ) -> dict:
        candles = self.mt5.get_candles(symbol, timeframe, candle_count)
        if candles and "error" in candles[0]:
            return candles[0]
        pip_sizes = {"JPY": 0.01}
        pip = 0.0001
        for key, val in pip_sizes.items():
            if key in symbol.upper():
                pip = val
                break
        return self.sd_detect.detect_zones(candles, pip_size=pip)

    def _handle_calculate_lot_size(
        self, symbol: str, stop_loss_pips: float, take_profit_pips: float
    ) -> dict:
        account = self.mt5.get_account_info()
        balance = account.get("balance", 25.0)
        xl_mode = self.xl.mode
        calc    = self.risk.calculate_lot(symbol, balance, xl_mode, stop_loss_pips, take_profit_pips)
        return {
            "symbol":           calc.symbol,
            "lot_size":         calc.lot_size,
            "risk_amount_usd":  calc.risk_amount,
            "risk_pct":         f"{calc.risk_pct:.1%}",
            "stop_loss_pips":   calc.stop_loss_pips,
            "take_profit_pips": calc.take_profit_pips,
            "rr_ratio":         f"1:{calc.rr_ratio}",
            "valid":            calc.valid,
            "rejection_reason": calc.rejection_reason,
            "explanation":      self.risk.explain_calculation(calc),
        }

    def _handle_validate_strategy(self, trades: list[dict]) -> dict:
        return self.validator.validate(trades)

    def _handle_execute_trade(
        self,
        symbol: str,
        direction: str,
        lots: float,
        stop_loss_price: float,
        take_profit_price: float,
        confluence_score: int,
        comment: str = "MJ_APEX",
    ) -> dict:
        # Gate checks
        account    = self.mt5.get_account_info()
        open_count = account.get("open_trades", len(self.mt5.get_open_positions()))
        minutes_to_news = self.calendar.minutes_to_next_news()
        xl_mode    = self.xl.mode

        allowed, issues = self.risk.pre_trade_gate(
            confluence_score, open_count, xl_mode, minutes_to_news
        )
        if not allowed:
            return {
                "success":  False,
                "blocked":  True,
                "issues":   issues,
                "xl_mode":  xl_mode,
                "xl":       self.xl.xl,
            }

        result = self.mt5.execute_trade(
            symbol, direction, lots, stop_loss_price, take_profit_price, comment
        )
        if result.get("success"):
            logger.info(
                "TRADE EXECUTED: %s %s %s lots | SL:%.5f TP:%.5f | XL:%d",
                direction, symbol, lots, stop_loss_price, take_profit_price, self.xl.xl,
            )
        return result

    def _handle_close_position(self, ticket: int) -> dict:
        return self.mt5.close_position(ticket)

    def _handle_modify_position(
        self, ticket: int, stop_loss: float, take_profit: float
    ) -> dict:
        return self.mt5.modify_position(ticket, stop_loss, take_profit)

    def _handle_get_xl_status(self) -> dict:
        return self.xl.status_report()

    def _handle_log_performance(
        self,
        ticket: int,
        symbol: str,
        outcome: str,
        profit_usd: float,
        account_balance: float,
    ) -> dict:
        if outcome == "win":
            result = self.xl.record_win(symbol, profit_usd, account_balance)
        else:
            result = self.xl.record_loss(symbol, profit_usd, account_balance)

        # Append to trade log file
        entry = {
            "ticket":          ticket,
            "symbol":          symbol,
            "outcome":         outcome,
            "profit_usd":      profit_usd,
            "account_balance": account_balance,
            "xl_after":        result["xl"],
            "mode_after":      result.get("mode"),
            "ts":              datetime.now(timezone.utc).isoformat(),
        }
        self._append_log("trade_log.jsonl", entry)

        return {
            "logged": True,
            "xl_after": result["xl"],
            "mode_after": result.get("mode"),
            "consecutive_losses": result.get("consecutive_losses", 0),
            "paused_until": result.get("paused_until"),
            "deleted": result.get("deleted", False),
            "world_bot_milestone": result.get("world_bot_milestone"),
        }

    def _handle_run_session_audit(self, last_n_trades: int = 10) -> dict:
        status = self.xl.status_report()
        history = status["last_10_trades"][-last_n_trades:]

        if not history:
            return {"warning": "No trade history found. Run trades first."}

        wins   = sum(1 for t in history if t["outcome"] == "win")
        losses = sum(1 for t in history if t["outcome"] == "loss")
        n      = len(history)
        wr     = wins / n if n > 0 else 0.0

        concerns = []
        if wr < 0.70:
            concerns.append(
                f"Win rate {wr:.0%} is below 70% threshold. Mandatory pause and recalibration."
            )

        consecutive = 0
        max_consec  = 0
        for t in history:
            if t["outcome"] == "loss":
                consecutive += 1
                max_consec = max(max_consec, consecutive)
            else:
                consecutive = 0
        if max_consec >= 3:
            concerns.append(f"Max {max_consec} consecutive losses detected in last {n} trades.")

        account = self.mt5.get_account_info()
        dd_check = self.risk.check_drawdown(
            account.get("balance", 25.0), status["peak_balance"]
        )

        return {
            "audit_period":       f"Last {n} trades",
            "win_rate":           f"{wr:.1%}",
            "wins":               wins,
            "losses":             losses,
            "max_consecutive_losses": max_consec,
            "xl_current":         status["xl"],
            "xl_mode":            status["mode"],
            "drawdown":           dd_check,
            "concerns":           concerns,
            "action_required":    len(concerns) > 0,
            "verdict": (
                "ALL CLEAR — strategy performing within parameters."
                if not concerns else
                "ACTION REQUIRED — address concerns before next trade."
            ),
        }

    # ── Utility ──────────────────────────────────────────────────────────────

    @staticmethod
    def _append_log(filename: str, entry: dict) -> None:
        with open(filename, "a") as f:
            f.write(json.dumps(entry) + "\n")

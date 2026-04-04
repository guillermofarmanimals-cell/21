"""
MJ APEX TRADER — MetaTrader 5 Bridge
All MT5 interactions: connection management, market data retrieval,
order execution, position management, and account info.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("mj_apex.mt5")

# ── Conditional MT5 import (graceful fallback for environments without MT5) ──
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package not available. Running in DEMO/simulation mode.")

# ── Timeframe mapping ────────────────────────────────────────────────────────
TF_MAP = {
    "M1": 1,  "M5": 5,  "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388, "D1": 16408,
    "W1": 32769, "MN1": 49153,
}


class MT5Bridge:
    """
    Thin wrapper around the MetaTrader5 Python API.
    Falls back to a simulation layer when MT5 is not installed or connected.
    """

    def __init__(self, login: int = 0, password: str = "", server: str = "") -> None:
        self.login    = login
        self.password = password
        self.server   = server
        self.connected = False
        self._sim_balance = 25.0    # simulation starting balance
        self._sim_positions: list[dict] = []
        self._sim_trade_counter = 1000

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.info("MT5 not available — using simulation mode.")
            self.connected = True   # simulation is always "connected"
            return True

        if not mt5.initialize():
            logger.error("MT5 initialize() failed: %s", mt5.last_error())
            return False

        if self.login:
            ok = mt5.login(self.login, password=self.password, server=self.server)
            if not ok:
                logger.error("MT5 login failed: %s", mt5.last_error())
                return False

        self.connected = True
        logger.info("MT5 connected. Account: %s", mt5.account_info()._asdict() if MT5_AVAILABLE else "sim")
        return True

    def disconnect(self) -> None:
        if MT5_AVAILABLE:
            mt5.shutdown()
        self.connected = False

    # ── Account Info ─────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        if not MT5_AVAILABLE:
            return self._sim_account_info()

        info = mt5.account_info()
        if info is None:
            return {"error": str(mt5.last_error())}
        return {
            "balance":      info.balance,
            "equity":       info.equity,
            "margin":       info.margin,
            "margin_free":  info.margin_free,
            "margin_level": info.margin_level,
            "profit":       info.profit,
            "currency":     info.currency,
            "leverage":     info.leverage,
            "open_trades":  len(mt5.positions_get() or []),
        }

    def _sim_account_info(self) -> dict:
        equity = self._sim_balance + sum(p.get("unrealized_pnl", 0) for p in self._sim_positions)
        return {
            "balance":      self._sim_balance,
            "equity":       round(equity, 2),
            "margin":       0.0,
            "margin_free":  self._sim_balance,
            "margin_level": 100.0,
            "profit":       round(equity - self._sim_balance, 2),
            "currency":     "USD",
            "leverage":     100,
            "open_trades":  len(self._sim_positions),
            "simulation":   True,
        }

    # ── Market Data ──────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[dict]:
        if not MT5_AVAILABLE:
            return self._sim_candles(symbol, timeframe, count)

        tf = TF_MAP.get(timeframe.upper())
        if tf is None:
            return [{"error": f"Unknown timeframe: {timeframe}"}]

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            return [{"error": str(mt5.last_error())}]

        candles = []
        for r in rates:
            candles.append({
                "time":   datetime.fromtimestamp(r["time"], tz=timezone.utc).isoformat(),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": int(r["tick_volume"]),
                "spread": int(r["spread"]),
            })
        return candles

    def _sim_candles(self, symbol: str, timeframe: str, count: int) -> list[dict]:
        """Minimal simulation: returns placeholder OHLCV data."""
        import random, time as _time
        base = 1.0850 if "EUR" in symbol.upper() else 1.0
        candles = []
        ts = int(_time.time()) - count * 3600
        for i in range(count):
            o = base + random.uniform(-0.005, 0.005)
            h = o + random.uniform(0, 0.003)
            l = o - random.uniform(0, 0.003)
            c = l + random.uniform(0, h - l)
            candles.append({
                "time":   datetime.fromtimestamp(ts + i * 3600, tz=timezone.utc).isoformat(),
                "open": round(o, 5), "high": round(h, 5),
                "low":  round(l, 5), "close": round(c, 5),
                "volume": random.randint(500, 5000),
                "spread": 2,
                "simulation": True,
            })
        return candles

    def get_current_price(self, symbol: str) -> Optional[dict]:
        if not MT5_AVAILABLE:
            return {"bid": 1.0849, "ask": 1.0851, "simulation": True}
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {"bid": tick.bid, "ask": tick.ask, "time": tick.time}

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_open_positions(self) -> list[dict]:
        if not MT5_AVAILABLE:
            return self._sim_positions.copy()

        positions = mt5.positions_get()
        if positions is None:
            return []
        result = []
        for p in positions:
            result.append({
                "ticket":     p.ticket,
                "symbol":     p.symbol,
                "type":       "BUY" if p.type == 0 else "SELL",
                "lots":       p.volume,
                "open_price": p.price_open,
                "sl":         p.sl,
                "tp":         p.tp,
                "profit":     p.profit,
                "swap":       p.swap,
                "open_time":  datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
                "comment":    p.comment,
            })
        return result

    # ── Order Execution ──────────────────────────────────────────────────────

    def execute_trade(
        self,
        symbol: str,
        direction: str,         # "BUY" or "SELL"
        lots: float,
        stop_loss: float,       # price level
        take_profit: float,     # price level
        comment: str = "MJ_APEX",
    ) -> dict:
        """Place a market order with mandatory SL and TP."""
        if not MT5_AVAILABLE:
            return self._sim_execute(symbol, direction, lots, stop_loss, take_profit, comment)

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None or not symbol_info.visible:
            mt5.symbol_select(symbol, True)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"success": False, "error": "Cannot get symbol tick"}

        order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if direction.upper() == "BUY" else tick.bid

        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      symbol,
            "volume":      lots,
            "type":        order_type,
            "price":       price,
            "sl":          stop_loss,
            "tp":          take_profit,
            "deviation":   20,
            "magic":       20250101,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                "success": False,
                "retcode": result.retcode,
                "error":   result.comment,
            }
        return {
            "success":  True,
            "ticket":   result.order,
            "price":    result.price,
            "volume":   result.volume,
            "symbol":   symbol,
            "direction": direction,
            "sl":        stop_loss,
            "tp":        take_profit,
        }

    def _sim_execute(
        self,
        symbol: str,
        direction: str,
        lots: float,
        stop_loss: float,
        take_profit: float,
        comment: str,
    ) -> dict:
        ticket = self._sim_trade_counter
        self._sim_trade_counter += 1
        price = 1.0851 if direction.upper() == "BUY" else 1.0849
        self._sim_positions.append({
            "ticket":     ticket,
            "symbol":     symbol,
            "type":       direction.upper(),
            "lots":       lots,
            "open_price": price,
            "sl":         stop_loss,
            "tp":         take_profit,
            "unrealized_pnl": 0.0,
            "open_time":  datetime.now(timezone.utc).isoformat(),
            "comment":    comment,
            "simulation": True,
        })
        logger.info("[SIM] Trade opened: %s %s %s lots @ %s | SL:%s TP:%s",
                    direction, symbol, lots, price, stop_loss, take_profit)
        return {
            "success":   True,
            "ticket":    ticket,
            "price":     price,
            "volume":    lots,
            "symbol":    symbol,
            "direction": direction,
            "sl":        stop_loss,
            "tp":        take_profit,
            "simulation": True,
        }

    # ── Close Position ───────────────────────────────────────────────────────

    def close_position(self, ticket: int) -> dict:
        if not MT5_AVAILABLE:
            return self._sim_close(ticket)

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "error": f"Position {ticket} not found"}

        pos = position[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      pos.symbol,
            "volume":      pos.volume,
            "type":        close_type,
            "position":    ticket,
            "price":       price,
            "deviation":   20,
            "magic":       20250101,
            "comment":     "MJ_APEX_CLOSE",
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "retcode": result.retcode, "error": result.comment}
        return {"success": True, "ticket": ticket, "close_price": result.price, "profit": pos.profit}

    def _sim_close(self, ticket: int) -> dict:
        for i, pos in enumerate(self._sim_positions):
            if pos["ticket"] == ticket:
                pnl = pos.get("unrealized_pnl", 0.0)
                self._sim_balance += pnl
                self._sim_positions.pop(i)
                logger.info("[SIM] Closed ticket %s | PnL: $%.2f", ticket, pnl)
                return {"success": True, "ticket": ticket, "profit": pnl, "simulation": True}
        return {"success": False, "error": f"Ticket {ticket} not found in simulation"}

    # ── Modify Position ──────────────────────────────────────────────────────

    def modify_position(self, ticket: int, sl: float, tp: float) -> dict:
        """Adjust stop loss and take profit. SL may only TIGHTEN, not widen."""
        if not MT5_AVAILABLE:
            for pos in self._sim_positions:
                if pos["ticket"] == ticket:
                    # Enforce: never move SL further away
                    if pos["type"] == "BUY" and sl < pos["sl"]:
                        return {"success": False, "error": "Cannot move SL further from entry (BUY)"}
                    if pos["type"] == "SELL" and sl > pos["sl"]:
                        return {"success": False, "error": "Cannot move SL further from entry (SELL)"}
                    pos["sl"] = sl
                    pos["tp"] = tp
                    return {"success": True, "ticket": ticket, "sl": sl, "tp": tp, "simulation": True}
            return {"success": False, "error": "Ticket not found"}

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "error": f"Position {ticket} not found"}
        pos = position[0]

        # Enforce: never move SL further away
        if pos.type == 0 and sl < pos.sl:  # BUY
            return {"success": False, "error": "Cannot move SL further from entry on a BUY"}
        if pos.type == 1 and sl > pos.sl:  # SELL
            return {"success": False, "error": "Cannot move SL further from entry on a SELL"}

        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl":       sl,
            "tp":       tp,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"success": False, "retcode": result.retcode, "error": result.comment}
        return {"success": True, "ticket": ticket, "sl": sl, "tp": tp}

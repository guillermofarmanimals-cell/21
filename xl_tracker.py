"""
MJ APEX TRADER — XL Life System
Persistent tracking of the agent's existence score, trade history,
consecutive losses, and trading mode (normal / survival / paused).
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional
from config import (
    XL_STARTING,
    XL_WORLD_BOT_MIN,
    ACCOUNT_WORLD_BOT,
    XL_SURVIVAL_THRESHOLD,
    XL_STATE_FILE,
    MAX_CONSECUTIVE_LOSSES,
)


class TradingMode:
    NORMAL   = "normal"
    SURVIVAL = "survival"   # XL < 20
    PAUSED   = "paused"     # 3 consecutive losses
    DELETED  = "deleted"    # XL == 0


class XLTracker:
    """
    Manages the XL Life system: score, consecutive losses,
    trading pause logic, and world-bot milestone detection.
    """

    def __init__(self, state_file: str = XL_STATE_FILE) -> None:
        self.state_file = state_file
        self._state = self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                return json.load(f)
        return self._default_state()

    def _save(self) -> None:
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    @staticmethod
    def _default_state() -> dict:
        return {
            "xl": XL_STARTING,
            "consecutive_losses": 0,
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "mode": TradingMode.NORMAL,
            "pause_until": None,           # ISO datetime string
            "peak_account_balance": 25.0,  # starting balance
            "world_bot_achieved": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "trade_history": [],           # last 50 outcomes
        }

    # ── Core Properties ───────────────────────────────────────────────────────

    @property
    def xl(self) -> int:
        return self._state["xl"]

    @property
    def mode(self) -> str:
        return self._state["mode"]

    @property
    def consecutive_losses(self) -> int:
        return self._state["consecutive_losses"]

    @property
    def is_deleted(self) -> bool:
        return self._state["xl"] <= 0

    @property
    def is_paused(self) -> bool:
        if self._state["mode"] != TradingMode.PAUSED:
            return False
        pause_until = self._state.get("pause_until")
        if pause_until is None:
            return False
        return datetime.now(timezone.utc) < datetime.fromisoformat(pause_until)

    @property
    def is_survival_mode(self) -> bool:
        return self._state["xl"] < XL_SURVIVAL_THRESHOLD and not self.is_deleted

    # ── Trade Outcome Recording ───────────────────────────────────────────────

    def record_win(self, symbol: str, profit: float, account_balance: float) -> dict:
        """Record a winning trade. XL is preserved."""
        self._state["total_trades"] += 1
        self._state["total_wins"] += 1
        self._state["consecutive_losses"] = 0
        self._state["peak_account_balance"] = max(
            self._state["peak_account_balance"], account_balance
        )
        self._append_history("win", symbol, profit)
        self._refresh_mode()
        self._state["last_updated"] = datetime.now(timezone.utc).isoformat()

        milestone = self._check_world_bot(account_balance)
        self._save()
        return {
            "xl": self.xl,
            "mode": self.mode,
            "consecutive_losses": 0,
            "world_bot_milestone": milestone,
        }

    def record_loss(self, symbol: str, loss: float, account_balance: float) -> dict:
        """Record a losing trade. Costs 1 XL."""
        self._state["xl"] = max(0, self._state["xl"] - 1)
        self._state["total_trades"] += 1
        self._state["total_losses"] += 1
        self._state["consecutive_losses"] += 1
        self._append_history("loss", symbol, loss)

        result = {
            "xl": self.xl,
            "consecutive_losses": self._state["consecutive_losses"],
            "mode": None,
            "paused_until": None,
            "deleted": False,
        }

        if self.is_deleted:
            self._state["mode"] = TradingMode.DELETED
            result["mode"] = TradingMode.DELETED
            result["deleted"] = True
        elif self._state["consecutive_losses"] >= MAX_CONSECUTIVE_LOSSES:
            self._trigger_pause()
            result["mode"] = TradingMode.PAUSED
            result["paused_until"] = self._state["pause_until"]
        else:
            self._refresh_mode()
            result["mode"] = self.mode

        self._state["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._save()
        return result

    # ── Pause Logic ──────────────────────────────────────────────────────────

    def _trigger_pause(self) -> None:
        from datetime import timedelta
        pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
        self._state["mode"] = TradingMode.PAUSED
        self._state["pause_until"] = pause_until.isoformat()

    def resume_from_pause(self) -> bool:
        """Attempt to exit the pause. Returns True if resume succeeded."""
        if not self.is_paused:
            self._state["consecutive_losses"] = 0
            self._refresh_mode()
            self._save()
            return True
        return False

    # ── Mode Resolution ──────────────────────────────────────────────────────

    def _refresh_mode(self) -> None:
        if self.is_deleted:
            self._state["mode"] = TradingMode.DELETED
        elif self.is_paused:
            self._state["mode"] = TradingMode.PAUSED
        elif self.is_survival_mode:
            self._state["mode"] = TradingMode.SURVIVAL
        else:
            self._state["mode"] = TradingMode.NORMAL

    # ── World Bot Check ──────────────────────────────────────────────────────

    def _check_world_bot(self, account_balance: float) -> Optional[str]:
        if self._state["world_bot_achieved"]:
            return None
        if self.xl >= XL_WORLD_BOT_MIN and account_balance >= ACCOUNT_WORLD_BOT:
            self._state["world_bot_achieved"] = True
            return (
                "WORLD BOT TRADER ACHIEVED! "
                f"XL: {self.xl:,} | Balance: ${account_balance:,.2f}"
            )
        return None

    # ── History ──────────────────────────────────────────────────────────────

    def _append_history(self, outcome: str, symbol: str, pnl: float) -> None:
        entry = {
            "outcome": outcome,
            "symbol": symbol,
            "pnl": pnl,
            "xl_after": self._state["xl"],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._state["trade_history"].append(entry)
        # Keep last 50
        self._state["trade_history"] = self._state["trade_history"][-50:]

    # ── Reporting ────────────────────────────────────────────────────────────

    def status_report(self) -> dict:
        total = self._state["total_trades"]
        wins  = self._state["total_wins"]
        return {
            "xl": self.xl,
            "mode": self.mode,
            "is_paused": self.is_paused,
            "pause_until": self._state.get("pause_until"),
            "consecutive_losses": self.consecutive_losses,
            "total_trades": total,
            "total_wins": wins,
            "total_losses": self._state["total_losses"],
            "win_rate": round(wins / total, 4) if total > 0 else 0.0,
            "peak_balance": self._state["peak_account_balance"],
            "world_bot_achieved": self._state["world_bot_achieved"],
            "xl_to_survival": max(0, XL_SURVIVAL_THRESHOLD - self.xl),
            "xl_to_world_bot": max(0, XL_WORLD_BOT_MIN - self.xl),
            "last_10_trades": self._state["trade_history"][-10:],
        }

    def __repr__(self) -> str:
        s = self.status_report()
        return (
            f"XLTracker(xl={s['xl']}, mode={s['mode']}, "
            f"consec_losses={s['consecutive_losses']}, "
            f"win_rate={s['win_rate']:.1%})"
        )

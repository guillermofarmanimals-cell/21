"""
MJ APEX TRADER — Risk Manager & Lot Size Calculator
Enforces the XL System risk tiers, survival mode, and all hard rules.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from config import (
    RISK_TIERS,
    SURVIVAL_MODE_RISK_PCT,
    SURVIVAL_MODE_MIN_CONF,
    MIN_CONFLUENCE_SCORE,
    MIN_RR_RATIO,
    MAX_OPEN_TRADES,
)
from xl_tracker import TradingMode


# ── Lot constraints by tier ──────────────────────────────────────────────────
LOT_CONSTRAINTS = {
    "micro":          {"min": 0.01, "max": 0.09,  "step": 0.01},
    "micro_mini":     {"min": 0.01, "max": 0.49,  "step": 0.01},
    "mini":           {"min": 0.01, "max": 0.99,  "step": 0.01},
    "mini_standard":  {"min": 0.01, "max": 4.99,  "step": 0.01},
    "standard":       {"min": 0.01, "max": 100.0, "step": 0.01},
}


@dataclass
class RiskProfile:
    balance: float
    risk_pct: float
    lot_tier: str
    risk_amount: float        # $ at risk this trade
    max_lot: float
    survival_mode: bool
    mode_label: str


@dataclass
class LotCalculation:
    symbol: str
    lot_size: float
    risk_amount: float
    risk_pct: float
    stop_loss_pips: float
    take_profit_pips: float
    rr_ratio: float
    pip_value_per_lot: float  # USD per pip per standard lot
    valid: bool
    rejection_reason: Optional[str]


class RiskManager:
    """
    All risk calculations for MJ APEX TRADER.
    Exposes lot sizing, trade gate checks, and post-trade drawdown checks.
    """

    # Approximate pip values per standard lot (USD) for common instruments.
    # Real production systems should fetch live values from the broker.
    PIP_VALUES: dict[str, float] = {
        # Forex majors/minors (USD counter or base)
        "EURUSD": 10.0, "GBPUSD": 10.0, "AUDUSD": 10.0, "NZDUSD": 10.0,
        "USDCAD": 7.5,  "USDCHF": 10.7, "USDJPY": 9.1,
        "GBPJPY": 9.1,  "EURJPY": 9.1,  "CADJPY": 9.1,
        # Indices (per point)
        "US30":   1.0,  "US500": 1.0,   "NAS100": 1.0,
        "GER40":  1.0,  "UK100": 1.0,
        # Commodities
        "XAUUSD": 10.0, "XAGUSD": 50.0,
        "USOIL":  10.0, "UKOIL":  10.0,
    }

    def __init__(self) -> None:
        pass

    # ── Risk Tier Lookup ─────────────────────────────────────────────────────

    def get_risk_profile(self, balance: float, xl_mode: str) -> RiskProfile:
        """Return the correct risk % and lot tier for current balance & XL mode."""
        survival = xl_mode in (TradingMode.SURVIVAL,)

        if survival:
            risk_pct  = SURVIVAL_MODE_RISK_PCT
            lot_tier  = "micro"
        else:
            tier = next(
                (t for t in RISK_TIERS if t["min"] <= balance < t["max"]),
                RISK_TIERS[-1],
            )
            risk_pct  = tier["risk_pct"]
            lot_tier  = tier["lot_tier"]

        risk_amount = balance * risk_pct
        max_lot = LOT_CONSTRAINTS[lot_tier]["max"]

        return RiskProfile(
            balance=balance,
            risk_pct=risk_pct,
            lot_tier=lot_tier,
            risk_amount=risk_amount,
            max_lot=max_lot,
            survival_mode=survival,
            mode_label=xl_mode,
        )

    # ── Lot Size Calculation ─────────────────────────────────────────────────

    def calculate_lot(
        self,
        symbol: str,
        balance: float,
        xl_mode: str,
        stop_loss_pips: float,
        take_profit_pips: float,
    ) -> LotCalculation:
        """
        Calculate the correct lot size given:
        - balance, XL mode → determines risk $ amount
        - stop_loss_pips   → determines pip risk
        - take_profit_pips → validates minimum 1:2 RR
        """
        profile = self.get_risk_profile(balance, xl_mode)
        pip_val = self._pip_value(symbol)

        rr = take_profit_pips / stop_loss_pips if stop_loss_pips > 0 else 0.0

        # Gate: minimum RR
        if rr < MIN_RR_RATIO:
            return LotCalculation(
                symbol=symbol, lot_size=0.0,
                risk_amount=profile.risk_amount, risk_pct=profile.risk_pct,
                stop_loss_pips=stop_loss_pips, take_profit_pips=take_profit_pips,
                rr_ratio=round(rr, 2), pip_value_per_lot=pip_val,
                valid=False,
                rejection_reason=(
                    f"RR {rr:.2f} is below minimum {MIN_RR_RATIO}:1. "
                    "Adjust TP or SL levels."
                ),
            )

        # lot_size = risk_$ / (SL_pips × pip_value_per_lot)
        if pip_val > 0 and stop_loss_pips > 0:
            raw_lot = profile.risk_amount / (stop_loss_pips * pip_val)
        else:
            raw_lot = 0.0

        constraints = LOT_CONSTRAINTS[profile.lot_tier]
        lot_size = self._round_to_step(raw_lot, constraints["step"])
        lot_size = max(constraints["min"], min(lot_size, constraints["max"]))

        return LotCalculation(
            symbol=symbol,
            lot_size=lot_size,
            risk_amount=round(profile.risk_amount, 2),
            risk_pct=profile.risk_pct,
            stop_loss_pips=stop_loss_pips,
            take_profit_pips=take_profit_pips,
            rr_ratio=round(rr, 2),
            pip_value_per_lot=pip_val,
            valid=True,
            rejection_reason=None,
        )

    # ── Pre-Trade Gate ───────────────────────────────────────────────────────

    def pre_trade_gate(
        self,
        confluence_score: int,
        open_trade_count: int,
        xl_mode: str,
        minutes_to_news: Optional[float],
    ) -> tuple[bool, list[str]]:
        """
        Returns (allowed, list_of_issues).
        ALL issues must be empty for a trade to execute.
        """
        issues: list[str] = []

        if xl_mode == TradingMode.DELETED:
            issues.append("XL = 0. Agent is permanently deleted. No trading permitted.")
            return False, issues

        if xl_mode == TradingMode.PAUSED:
            issues.append("Trading is paused (3 consecutive losses). 24-hour cooldown active.")
            return False, issues

        min_conf = SURVIVAL_MODE_MIN_CONF if xl_mode == TradingMode.SURVIVAL else MIN_CONFLUENCE_SCORE
        if confluence_score < min_conf:
            issues.append(
                f"Confluence score {confluence_score}/5 below minimum {min_conf}/5. "
                "Stand down. Wait for better setup."
            )

        if open_trade_count >= MAX_OPEN_TRADES:
            issues.append(
                f"Already at maximum {MAX_OPEN_TRADES} open trades. Cannot open more."
            )

        if minutes_to_news is not None and minutes_to_news < 15:
            issues.append(
                f"High-impact news event in {minutes_to_news:.0f} minutes. "
                "News blackout in effect (±15 min)."
            )

        return len(issues) == 0, issues

    # ── Drawdown Check ───────────────────────────────────────────────────────

    def check_drawdown(
        self,
        current_balance: float,
        peak_balance: float,
        max_drawdown_pct: float = 0.20,
    ) -> dict:
        """Check if current drawdown violates the 20% strategy limit."""
        drawdown = (peak_balance - current_balance) / peak_balance if peak_balance > 0 else 0.0
        return {
            "drawdown_pct": round(drawdown, 4),
            "drawdown_usd": round(peak_balance - current_balance, 2),
            "breach": drawdown >= max_drawdown_pct,
            "peak": peak_balance,
            "current": current_balance,
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _pip_value(self, symbol: str) -> float:
        sym = symbol.upper().replace(".", "").replace("#", "").replace(" ", "")
        return self.PIP_VALUES.get(sym, 10.0)

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        return round(round(value / step) * step, 10)

    # ── Summary ──────────────────────────────────────────────────────────────

    def explain_calculation(self, calc: LotCalculation) -> str:
        if not calc.valid:
            return f"REJECTED: {calc.rejection_reason}"
        return (
            f"Symbol: {calc.symbol} | "
            f"Lot: {calc.lot_size} | "
            f"Risk: ${calc.risk_amount} ({calc.risk_pct:.1%}) | "
            f"SL: {calc.stop_loss_pips} pips | "
            f"TP: {calc.take_profit_pips} pips | "
            f"RR: 1:{calc.rr_ratio} | "
            f"Pip value: ${calc.pip_value_per_lot}/lot"
        )

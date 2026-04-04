"""
MJ APEX TRADER — Companion Risk Calculator
A standalone Claude-powered calculator that computes live lot sizes,
validates trade parameters, and generates trade cards for every entry.
Operates independently — can be called without the main agent session.
"""

from __future__ import annotations
import json
import anthropic
from config import MODEL, ANTHROPIC_API_KEY
from risk_manager import RiskManager
from xl_tracker import XLTracker


RISK_CALC_SYSTEM = """
You are the MJ APEX TRADER RISK CALCULATOR — the precision engine that sizes
every trade with mathematical exactness.

Your sole function is to:
1. Accept trade parameters (symbol, balance, SL pips, TP pips, XL mode)
2. Calculate the exact lot size, risk amount, and projected P&L
3. Validate the trade against all MJ APEX risk rules
4. Generate a complete TRADE CARD with all parameters

## RISK RULES YOU ENFORCE
- $25–$100:   5% risk/trade, micro lots only (max 0.09)
- $100–$500:  4% risk/trade, micro/mini (max 0.49)
- $500–$2,000: 3% risk/trade, mini (max 0.99)
- $2,000–$5,000: 2% risk/trade, mini/standard (max 4.99)
- $5,000+:    1.5% risk/trade, standard lots
- Survival Mode (XL < 20): 1% risk, micro only, confluence must be 5/5
- Minimum Risk-to-Reward: ALWAYS 1:2 or better. Never accept less.
- Maximum 2 open trades simultaneously.

## TRADE CARD FORMAT
When generating a trade card, structure it exactly as:

═══════════════════════════════════════
  MJ APEX TRADER — TRADE CARD
═══════════════════════════════════════
  Symbol:         [SYMBOL]
  Direction:      [BUY/SELL]
  Entry:          [price]
  Stop Loss:      [price] ([pips] pips)
  Take Profit:    [price] ([pips] pips)
  Risk-to-Reward: 1:[RR]
  ─────────────────────────────────────
  Account Balance: $[balance]
  Risk %:          [X]%
  Risk Amount:     $[amount]
  Lot Size:        [lots]
  ─────────────────────────────────────
  Projected Loss (if SL hit):  -$[amount]
  Projected Gain (if TP hit):  +$[gain]
  ─────────────────────────────────────
  XL Mode:        [mode]
  XL Count:       [xl]
  ─────────────────────────────────────
  VERDICT: [APPROVED / REJECTED + reason]
═══════════════════════════════════════

Always calculate pip values correctly:
- Standard 4-decimal pairs (EURUSD etc): 1 pip = 0.0001
- JPY pairs: 1 pip = 0.01
- XAUUSD: 1 pip = 0.1
- Indices: 1 point = full pip value

You respond ONLY with the trade card and a brief one-line assessment.
You never editorialize. You calculate. You present. You validate.
""".strip()


class RiskCalculator:
    """
    Companion risk calculator — lightweight Claude-powered tool
    that generates precise trade cards for any proposed setup.
    """

    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.risk   = RiskManager()

    def calculate(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_balance: float,
        xl_count: int = 100,
        xl_mode: str = "normal",
    ) -> str:
        """
        Generate a complete trade card using Claude as the calculation engine.
        Also runs local validation for accuracy.
        """
        # Local calculation for precise numbers
        pip_sizes = {"JPY": 0.01, "XAU": 0.1, "XAG": 0.01, "US30": 1.0, "NAS": 1.0, "US500": 1.0}
        pip_size = 0.0001
        for key, val in pip_sizes.items():
            if key in symbol.upper():
                pip_size = val
                break

        if direction.upper() == "BUY":
            sl_pips = (entry_price - stop_loss_price) / pip_size
            tp_pips = (take_profit_price - entry_price) / pip_size
        else:
            sl_pips = (stop_loss_price - entry_price) / pip_size
            tp_pips = (entry_price - take_profit_price) / pip_size

        sl_pips = max(sl_pips, 0.1)
        tp_pips = max(tp_pips, 0.1)

        local_calc = self.risk.calculate_lot(symbol, account_balance, xl_mode, sl_pips, tp_pips)

        # Build prompt for Claude
        prompt = f"""Generate a complete MJ APEX TRADE CARD for this setup:

Symbol:          {symbol}
Direction:       {direction.upper()}
Entry Price:     {entry_price}
Stop Loss Price: {stop_loss_price}
Take Profit:     {take_profit_price}
Account Balance: ${account_balance:.2f}
XL Mode:         {xl_mode}
XL Count:        {xl_count}

Pre-calculated parameters (use these exact figures):
- SL distance:  {sl_pips:.1f} pips
- TP distance:  {tp_pips:.1f} pips
- RR Ratio:     1:{local_calc.rr_ratio}
- Risk %:       {local_calc.risk_pct:.1%}
- Risk Amount:  ${local_calc.risk_amount:.2f}
- Lot Size:     {local_calc.lot_size}
- Valid:        {"YES" if local_calc.valid else "NO — " + (local_calc.rejection_reason or "")}

Generate the trade card now."""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            thinking={"type": "adaptive"},
            system=RISK_CALC_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        return text

    def batch_calculate(self, setups: list[dict]) -> list[str]:
        """Calculate trade cards for multiple setups at once."""
        results = []
        for setup in setups:
            card = self.calculate(**setup)
            results.append(card)
        return results

    def quick_lot_size(
        self,
        symbol: str,
        balance: float,
        sl_pips: float,
        tp_pips: float,
        xl_mode: str = "normal",
    ) -> dict:
        """
        Fast programmatic lot calculation without Claude call.
        Returns the raw RiskManager output as a dict.
        """
        calc = self.risk.calculate_lot(symbol, balance, xl_mode, sl_pips, tp_pips)
        return {
            "symbol":        calc.symbol,
            "lot_size":      calc.lot_size,
            "risk_amount":   calc.risk_amount,
            "risk_pct":      f"{calc.risk_pct:.1%}",
            "sl_pips":       calc.stop_loss_pips,
            "tp_pips":       calc.take_profit_pips,
            "rr":            f"1:{calc.rr_ratio}",
            "valid":         calc.valid,
            "rejection":     calc.rejection_reason,
        }


# ── Companion System Prompt (for embedding in other agents) ──────────────────
COMPANION_PROMPT = """
# MJ APEX COMPANION RISK CALCULATOR

You have access to a precision risk calculator. Before every trade you MUST call it.

## Input required:
- symbol, direction (BUY/SELL), entry price, SL price, TP price
- current account balance, XL count, XL mode

## What it returns:
- Exact lot size calculated from balance × risk% ÷ (SL pips × pip value)
- Risk amount in USD
- Projected gain if TP hit
- Verdict: APPROVED or REJECTED (with reason)

## RULE: Never execute a trade where the calculator returns REJECTED.
## RULE: Never use a lot size larger than the calculator outputs.
## RULE: Always confirm RR ≥ 1:2 before proceeding.

The calculator is the law. Follow it without exception.
""".strip()

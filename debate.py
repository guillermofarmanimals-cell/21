"""
MJ APEX TRADER — Multi-Agent Trade Debate Engine
Inspired by TradingAgents (github.com/TauricResearch/TradingAgents).

Before any trade is executed, three specialist agents debate:
  BullResearcher  — argues FOR entry
  BearResearcher  — argues AGAINST entry
  RiskManager     — stress-tests position sizing and invalidation
  TraderAgent     — synthesises and delivers final EXECUTE / STAND DOWN verdict
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from config import MODEL, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID

logger = logging.getLogger("mj_apex.debate")

# ── Debate system prompts ─────────────────────────────────────────────────────

_BULL_PROMPT = """You are the BULL RESEARCHER for MJ APEX TRADER.
Your job: build the strongest possible case FOR entering this trade.
Focus on: confluence signals that support the move, key support/demand zones,
momentum, trend alignment, risk:reward, and why this is a high-probability setup.
Be specific. Use the data provided. 200 words max."""

_BEAR_PROMPT = """You are the BEAR RESEARCHER for MJ APEX TRADER.
Your job: build the strongest possible case AGAINST entering this trade.
Focus on: counter-signals, nearby resistance/supply zones, weak confluence,
news risk, spread cost, overextended moves, and why this trade could fail.
Be specific. Use the data provided. 200 words max."""

_RISK_PROMPT = """You are the RISK MANAGER for MJ APEX TRADER.
Your job: stress-test the risk parameters of this trade.
Evaluate: stop loss placement (is it logical or too tight/wide?), risk:reward ratio
(is it genuinely ≥1:2?), position size relative to account balance, maximum
adverse excursion risk, and whether the invalidation level is clear.
Flag any red flags. 200 words max."""

_TRADER_PROMPT = """You are the HEAD TRADER for MJ APEX TRADER.
You have received arguments from the Bull Researcher, Bear Researcher, and Risk Manager.
Your job: weigh all three perspectives and deliver a final verdict.

Respond in this exact format:

VERDICT: EXECUTE or STAND DOWN
CONFIDENCE: X/10
REASON: (2-3 sentences explaining the decision)
ADJUSTED_SL: (suggested SL price if executing, else N/A)
ADJUSTED_TP: (suggested TP price if executing, else N/A)
MIN_CONFLUENCE: (confluence score required before pulling trigger, 3-5)"""


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DebateResult:
    verdict: str          # "EXECUTE" or "STAND DOWN"
    confidence: int       # 1-10
    reason: str
    adjusted_sl: Optional[str]
    adjusted_tp: Optional[str]
    min_confluence: int
    bull_case: str
    bear_case: str
    risk_assessment: str
    raw_trader_response: str

    @property
    def should_execute(self) -> bool:
        return self.verdict == "EXECUTE" and self.confidence >= 6

    def summary(self) -> str:
        verdict_icon = "✅" if self.should_execute else "🛑"
        return (
            f"{verdict_icon} DEBATE VERDICT: {self.verdict} ({self.confidence}/10)\n"
            f"Reason: {self.reason}\n"
            f"SL: {self.adjusted_sl or 'N/A'}  |  TP: {self.adjusted_tp or 'N/A'}\n"
            f"Min confluence required: {self.min_confluence}/5\n\n"
            f"--- BULL CASE ---\n{self.bull_case}\n\n"
            f"--- BEAR CASE ---\n{self.bear_case}\n\n"
            f"--- RISK ASSESSMENT ---\n{self.risk_assessment}"
        )


# ── Debate Engine ─────────────────────────────────────────────────────────────

class TradeDebateEngine:
    """
    Runs a three-way debate before any trade entry.
    Each agent gets one turn; the Trader synthesises and decides.
    """

    def __init__(self) -> None:
        extra_headers = {}
        if ANTHROPIC_WORKSPACE_ID:
            extra_headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            default_headers=extra_headers if extra_headers else None,
        )

    def _call(self, system: str, user_msg: str) -> str:
        """Single-turn, non-streaming call for debate agents."""
        resp = self.client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text if resp.content else ""

    def run(
        self,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        confluence_score: int,
        analysis_context: str,
    ) -> DebateResult:
        """
        Run the full debate for a proposed trade.
        Returns a DebateResult with final verdict.
        """
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        trade_brief = (
            f"PROPOSED TRADE\n"
            f"Symbol: {symbol} | Direction: {direction}\n"
            f"Entry: {entry} | Stop Loss: {sl} | Take Profit: {tp}\n"
            f"Risk:Reward: 1:{rr:.1f} | Confluence Score: {confluence_score}/5\n\n"
            f"MARKET ANALYSIS:\n{analysis_context}"
        )

        logger.info("Debate started: %s %s @ %s", direction, symbol, entry)

        bull_case      = self._call(_BULL_PROMPT, trade_brief)
        bear_case      = self._call(_BEAR_PROMPT, trade_brief)
        risk_case      = self._call(_RISK_PROMPT, trade_brief)

        trader_input = (
            f"{trade_brief}\n\n"
            f"=== BULL RESEARCHER ===\n{bull_case}\n\n"
            f"=== BEAR RESEARCHER ===\n{bear_case}\n\n"
            f"=== RISK MANAGER ===\n{risk_case}"
        )
        trader_response = self._call(_TRADER_PROMPT, trader_input)

        result = self._parse_trader_response(
            trader_response, bull_case, bear_case, risk_case
        )
        logger.info("Debate verdict: %s (%d/10)", result.verdict, result.confidence)
        return result

    @staticmethod
    def _parse_trader_response(
        raw: str, bull: str, bear: str, risk: str
    ) -> DebateResult:
        lines = {
            line.split(":")[0].strip(): ":".join(line.split(":")[1:]).strip()
            for line in raw.splitlines()
            if ":" in line
        }

        verdict   = "EXECUTE" if "EXECUTE" in lines.get("VERDICT", "").upper() else "STAND DOWN"
        try:
            confidence = int("".join(filter(str.isdigit, lines.get("CONFIDENCE", "5")))[:2])
            confidence = max(1, min(10, confidence))
        except ValueError:
            confidence = 5

        try:
            min_conf = int("".join(filter(str.isdigit, lines.get("MIN_CONFLUENCE", "3")))[:1])
            min_conf = max(3, min(5, min_conf))
        except ValueError:
            min_conf = 3

        return DebateResult(
            verdict=verdict,
            confidence=confidence,
            reason=lines.get("REASON", raw[:200]),
            adjusted_sl=lines.get("ADJUSTED_SL") or None,
            adjusted_tp=lines.get("ADJUSTED_TP") or None,
            min_confluence=min_conf,
            bull_case=bull,
            bear_case=bear,
            risk_assessment=risk,
            raw_trader_response=raw,
        )

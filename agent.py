"""
MJ APEX TRADER — Core Agent
Runs the autonomous agentic loop using Claude claude-opus-4-6 with adaptive thinking.
Streams responses to the console and handles all tool calls.
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config import MODEL, SYSTEM_PROMPT, ANTHROPIC_API_KEY, ANTHROPIC_WORKSPACE_ID
from mt5_bridge import MT5Bridge
from risk_manager import RiskManager
from xl_tracker import XLTracker, TradingMode
from tools import ToolHandler, TOOL_DEFINITIONS

logger = logging.getLogger("mj_apex.agent")


class MJApexTrader:
    """
    The MJ APEX TRADER autonomous agent.
    Maintains conversation history and drives the agentic loop:
    Claude reasons → calls tools → receives results → reasons again → ...
    until stop_reason == "end_turn".
    """

    MAX_TOOL_ITERATIONS = 30   # safety cap per session prompt

    def __init__(
        self,
        mt5: MT5Bridge,
        risk: RiskManager,
        xl: XLTracker,
        verbose: bool = True,
    ) -> None:
        extra_headers = {}
        if ANTHROPIC_WORKSPACE_ID:
            extra_headers["anthropic-workspace-id"] = ANTHROPIC_WORKSPACE_ID
        self.client  = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            default_headers=extra_headers if extra_headers else None,
        )
        self.tools_h = ToolHandler(mt5, risk, xl)
        self.xl      = xl
        self.verbose = verbose
        self.messages: list[dict] = []

    # ── Session Management ────────────────────────────────────────────────────

    def new_session(self) -> None:
        """Reset conversation history for a fresh session."""
        self.messages = []
        logger.info("New session started. XL: %d | Mode: %s", self.xl.xl, self.xl.mode)

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    # ── Main Query Entry Point ────────────────────────────────────────────────

    def query(self, user_prompt: str, stream: bool = True) -> str:
        """
        Send a user prompt to MJ APEX TRADER and run the full agentic loop.
        Returns the agent's final text response.
        """
        # Abort if agent is deleted
        if self.xl.is_deleted:
            msg = (
                "XL HAS REACHED 0. MJ APEX TRADER IS PERMANENTLY DELETED. "
                "No trading activity is permitted. The agent's existence has ended."
            )
            self._print(msg)
            return msg

        # Abort if paused
        if self.xl.is_paused:
            status = self.xl.status_report()
            msg = (
                f"Trading is currently PAUSED after {self.xl.consecutive_losses} consecutive losses.\n"
                f"Pause expires: {status.get('pause_until', 'unknown')}\n"
                "Use this time to run run_session_audit and identify root causes."
            )
            self._print(msg)
            return msg

        self.add_user_message(user_prompt)

        final_response = ""
        iterations = 0

        while iterations < self.MAX_TOOL_ITERATIONS:
            iterations += 1

            if stream:
                response_text, tool_calls, stop_reason = self._stream_turn()
            else:
                response_text, tool_calls, stop_reason = self._standard_turn()

            final_response = response_text

            # Append assistant response to history
            # We reconstruct content blocks for proper multi-turn handling
            assistant_content = self._build_assistant_content(response_text, tool_calls)
            self.messages.append({"role": "assistant", "content": assistant_content})

            if stop_reason == "end_turn" or not tool_calls:
                break

            # Execute all tool calls and collect results
            tool_results = self._execute_tools(tool_calls)
            self.messages.append({"role": "user", "content": tool_results})

        if iterations >= self.MAX_TOOL_ITERATIONS:
            logger.warning("Hit MAX_TOOL_ITERATIONS (%d). Forcing end.", self.MAX_TOOL_ITERATIONS)

        return final_response

    # ── Streaming Turn ────────────────────────────────────────────────────────

    def _stream_turn(self) -> tuple[str, list[dict], str]:
        """
        Execute one turn with streaming. Returns (text, tool_use_blocks, stop_reason).
        """
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        stop_reason = "end_turn"

        # Buffer for tool input JSON (streamed incrementally)
        current_tool: Optional[dict] = None
        tool_input_buffer: dict[int, str] = {}

        with self.client.messages.stream(
            model=MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=self.messages,
        ) as stream:
            for event in stream:
                etype = event.type

                if etype == "content_block_start":
                    blk = event.content_block
                    if blk.type == "tool_use":
                        current_tool = {"id": blk.id, "name": blk.name, "input": ""}
                        tool_input_buffer[event.index] = ""
                    elif blk.type == "thinking" and self.verbose:
                        self._print("\n[Thinking...]", end="")

                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_parts.append(delta.text)
                        if self.verbose:
                            print(delta.text, end="", flush=True)
                    elif delta.type == "thinking_delta":
                        pass  # thinking is internal; don't echo
                    elif delta.type == "input_json_delta":
                        if event.index in tool_input_buffer:
                            tool_input_buffer[event.index] += delta.partial_json

                elif etype == "content_block_stop":
                    if current_tool is not None and event.index in tool_input_buffer:
                        raw_input = tool_input_buffer.pop(event.index, "{}")
                        try:
                            current_tool["input"] = json.loads(raw_input) if raw_input else {}
                        except json.JSONDecodeError:
                            current_tool["input"] = {}
                        tool_calls.append(current_tool)
                        current_tool = None

                elif etype == "message_delta":
                    stop_reason = getattr(event.delta, "stop_reason", "end_turn") or "end_turn"

        if text_parts and self.verbose:
            print()  # newline after streamed output

        return "".join(text_parts), tool_calls, stop_reason

    # ── Non-Streaming Turn ────────────────────────────────────────────────────

    def _standard_turn(self) -> tuple[str, list[dict], str]:
        """Non-streaming turn for programmatic / batch use."""
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=self.messages,
        )

        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id":    block.id,
                    "name":  block.name,
                    "input": block.input,
                })

        return "".join(text_parts), tool_calls, response.stop_reason or "end_turn"

    # ── Tool Execution ────────────────────────────────────────────────────────

    def _execute_tools(self, tool_calls: list[dict]) -> list[dict]:
        """Execute all tool calls and return tool_result content blocks."""
        results = []
        for call in tool_calls:
            name  = call["name"]
            inp   = call["input"]
            tid   = call["id"]

            self._print(f"\n[TOOL] {name}({json.dumps(inp, ensure_ascii=False)[:120]})")

            result = self.tools_h.execute(name, inp)
            result_str = json.dumps(result, ensure_ascii=False, default=str)

            self._print(f"[RESULT] {result_str[:300]}{'...' if len(result_str) > 300 else ''}")

            results.append({
                "type":        "tool_result",
                "tool_use_id": tid,
                "content":     result_str,
            })

        return results

    # ── Message Content Builder ───────────────────────────────────────────────

    @staticmethod
    def _build_assistant_content(text: str, tool_calls: list[dict]) -> list[dict]:
        """Build the properly-typed assistant content blocks for history."""
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for call in tool_calls:
            blocks.append({
                "type":  "tool_use",
                "id":    call["id"],
                "name":  call["name"],
                "input": call["input"],
            })
        # Fallback: if nothing generated, add empty text block
        if not blocks:
            blocks.append({"type": "text", "text": ""})
        return blocks

    # ── Utility ──────────────────────────────────────────────────────────────

    def _print(self, msg: str, end: str = "\n") -> None:
        if self.verbose:
            print(msg, end=end, flush=True)

    # ── Session Opener ────────────────────────────────────────────────────────

    def open_session(self) -> str:
        """
        Run the full Intelligence Gathering Protocol at session start.
        Primes the agent with current market conditions.
        """
        self.new_session()
        prompt = (
            "MJ APEX TRADER — INITIATING SESSION.\n\n"
            "Run the complete Intelligence Gathering Protocol:\n"
            "1. Check XL status and confirm trading mode\n"
            "2. Get account information\n"
            "3. Check all open positions\n"
            "4. Get the economic calendar for the next 24 hours\n"
            "5. Get market sentiment (Fear & Greed)\n"
            "6. Check lunar cycle and planetary alerts\n"
            "7. Get COT report for EURUSD and XAUUSD\n"
            "8. Analyze EURUSD H4 and D1 for structure, supply/demand zones\n"
            "9. Analyze XAUUSD H4 and D1 for structure, supply/demand zones\n\n"
            "After gathering all intelligence, provide:\n"
            "- Session brief: market conditions, bias for each asset\n"
            "- Any immediate trade setups that score ≥3 confluence\n"
            "- XL status and risk parameters for this session\n"
            "- Any concerns or reasons to stand down\n\n"
            "Speak as MJ APEX TRADER. Be decisive and precise."
        )
        return self.query(prompt)

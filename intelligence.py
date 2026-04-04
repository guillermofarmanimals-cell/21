"""
MJ APEX TRADER — Intelligence Gathering Module
Covers: Economic calendar, COT reports, sentiment, lunar/planetary cycles,
supply/demand zone detection, and backtest validation engine.
"""

from __future__ import annotations
import json
import math
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("mj_apex.intelligence")


# ── Economic Calendar ────────────────────────────────────────────────────────

class EconomicCalendar:
    """
    Fetches upcoming high-impact news events.
    Uses ForexFactory-style data where available; falls back to static stubs.
    """

    # Public endpoint (no key required) — can swap for a premium data provider
    BASE_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def get_upcoming(self, hours_ahead: int = 24) -> list[dict]:
        try:
            resp = requests.get(self.BASE_URL, timeout=8)
            resp.raise_for_status()
            events = resp.json()
        except Exception as exc:
            logger.warning("Economic calendar fetch failed: %s", exc)
            return self._stub_events()

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        high_impact = []

        for ev in events:
            if ev.get("impact", "").lower() != "high":
                continue
            try:
                ts_str = ev.get("date", "") or ev.get("datetime", "")
                event_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue

            if now <= event_dt <= cutoff:
                minutes_away = (event_dt - now).total_seconds() / 60
                high_impact.append({
                    "title":        ev.get("title", "Unknown"),
                    "currency":     ev.get("country", ""),
                    "impact":       "HIGH",
                    "datetime_utc": event_dt.isoformat(),
                    "minutes_away": round(minutes_away, 1),
                    "in_blackout":  minutes_away < 15,
                    "forecast":     ev.get("forecast", ""),
                    "previous":     ev.get("previous", ""),
                })

        high_impact.sort(key=lambda x: x["minutes_away"])
        return high_impact

    def minutes_to_next_news(self) -> Optional[float]:
        events = self.get_upcoming(hours_ahead=1)
        if not events:
            return None
        return events[0]["minutes_away"]

    def _stub_events(self) -> list[dict]:
        """Return placeholder when network unavailable."""
        return [
            {
                "title": "STUB — Economic Calendar Unavailable",
                "currency": "N/A", "impact": "HIGH",
                "datetime_utc": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                "minutes_away": 240.0,
                "in_blackout": False,
                "forecast": "N/A", "previous": "N/A",
                "warning": "Live calendar unavailable. Treat with caution.",
            }
        ]


# ── COT Report ───────────────────────────────────────────────────────────────

class COTAnalyzer:
    """
    Commitment of Traders report analysis.
    CFTC publishes the full report on Fridays. This module fetches the
    weekly Quandl/Nasdaq Data Link feed (free tier) or falls back to stubs.
    """

    QUANDL_BASE = "https://data.nasdaq.com/api/v3/datasets/CFTC"

    # CFTC codes for common instruments
    CODES = {
        "EURUSD": "099741_FO",
        "GBPUSD": "096742_FO",
        "USDJPY": "097741_FO",
        "XAUUSD": "088691_FO",
        "USOIL":  "067651_FO",
        "US500":  "13874P_FO",
    }

    def get_positioning(self, symbol: str, api_key: str = "") -> dict:
        code = self.CODES.get(symbol.upper().replace(".", ""))
        if not code or not api_key:
            return self._stub_cot(symbol)

        url = f"{self.QUANDL_BASE}/{code}.json"
        params = {"rows": 2, "api_key": api_key}
        try:
            resp = requests.get(url, params=params, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            rows = data["dataset"]["data"]
            if len(rows) < 2:
                return self._stub_cot(symbol)

            latest, prior = rows[0], rows[1]
            cols = data["dataset"]["column_names"]
            idx = {c: i for i, c in enumerate(cols)}

            def val(row, name):
                return row[idx[name]] if name in idx else 0

            comm_long   = val(latest, "Commercial Long")
            comm_short  = val(latest, "Commercial Short")
            noncomm_long = val(latest, "Noncommercial Long")
            noncomm_short = val(latest, "Noncommercial Short")

            net_comm    = comm_long - comm_short
            net_noncomm = noncomm_long - noncomm_short
            prior_net_noncomm = val(prior, "Noncommercial Long") - val(prior, "Noncommercial Short")

            return {
                "symbol":           symbol,
                "report_date":      latest[0],
                "commercial_long":  comm_long,
                "commercial_short": comm_short,
                "net_commercial":   net_comm,
                "noncomm_long":     noncomm_long,
                "noncomm_short":    noncomm_short,
                "net_noncommercial": net_noncomm,
                "noncomm_change":   net_noncomm - prior_net_noncomm,
                "bias": "BULLISH" if net_noncomm > prior_net_noncomm else "BEARISH",
                "smart_money_direction": "LONG" if net_comm > 0 else "SHORT",
            }
        except Exception as exc:
            logger.warning("COT fetch failed: %s", exc)
            return self._stub_cot(symbol)

    def _stub_cot(self, symbol: str) -> dict:
        return {
            "symbol":            symbol,
            "report_date":       "2025-03-28",
            "net_noncommercial": 42500,
            "noncomm_change":    3100,
            "bias":              "BULLISH",
            "smart_money_direction": "LONG",
            "note": "Stub data — provide QUANDL_API_KEY env var for live COT feed.",
        }


# ── Market Sentiment ─────────────────────────────────────────────────────────

class SentimentAnalyzer:
    """
    Fear & Greed index (CNN / Alternative.me) and retail sentiment proxy.
    """

    FNG_URL = "https://api.alternative.me/fng/"

    def get_fear_greed(self) -> dict:
        try:
            resp = requests.get(self.FNG_URL, timeout=6)
            resp.raise_for_status()
            data = resp.json()["data"][0]
            value = int(data["value"])
            classification = data["value_classification"]
            return {
                "score":          value,
                "label":          classification,
                "interpretation": self._interpret(value),
                "timestamp":      data.get("timestamp"),
            }
        except Exception as exc:
            logger.warning("Fear & Greed fetch failed: %s", exc)
            return {"score": 50, "label": "Neutral", "interpretation": "Unavailable", "note": str(exc)}

    @staticmethod
    def _interpret(score: int) -> str:
        if score <= 25: return "EXTREME_FEAR — potential buying opportunity"
        if score <= 45: return "FEAR — cautious; look for bottoms"
        if score <= 55: return "NEUTRAL — no strong directional bias"
        if score <= 75: return "GREED — be selective on longs; watch for reversals"
        return "EXTREME_GREED — high reversal risk; reduce long exposure"


# ── Lunar & Planetary Cycles ─────────────────────────────────────────────────

class CycleAnalyzer:
    """
    Lunar phase calculations (astronomical formulas, no external API needed).
    Also tracks Mercury retrograde windows and Jubilee cycle awareness.
    """

    # Known Mercury retrograde periods (approximate) 2025–2026
    MERCURY_RETROGRADE = [
        ("2025-03-15", "2025-04-07"),
        ("2025-07-18", "2025-08-11"),
        ("2025-11-09", "2025-12-01"),
        ("2026-03-11", "2026-04-02"),
    ]

    def get_lunar_phase(self, date: Optional[datetime] = None) -> dict:
        if date is None:
            date = datetime.now(timezone.utc)

        # Approximation: known new moon + 29.53-day synodic period
        known_new_moon = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
        synodic = 29.53058867
        days_since = (date - known_new_moon).total_seconds() / 86400
        cycle_pos  = days_since % synodic
        illumination = (1 - math.cos(2 * math.pi * cycle_pos / synodic)) / 2

        if cycle_pos < 1.85:
            phase = "New Moon"
        elif cycle_pos < 7.38:
            phase = "Waxing Crescent"
        elif cycle_pos < 9.22:
            phase = "First Quarter"
        elif cycle_pos < 14.76:
            phase = "Waxing Gibbous"
        elif cycle_pos < 16.61:
            phase = "Full Moon"
        elif cycle_pos < 22.15:
            phase = "Waning Gibbous"
        elif cycle_pos < 23.99:
            phase = "Last Quarter"
        else:
            phase = "Waning Crescent"

        days_to_next_new  = synodic - cycle_pos
        days_to_next_full = (synodic / 2 - cycle_pos) % synodic

        volatility_window = phase in ("New Moon", "Full Moon") or (
            min(days_to_next_new, days_to_next_full) <= 2
        )

        return {
            "phase":            phase,
            "cycle_position":   round(cycle_pos, 2),
            "illumination_pct": round(illumination * 100, 1),
            "days_to_new_moon": round(days_to_next_new, 1),
            "days_to_full_moon": round(days_to_next_full, 1),
            "volatility_window": volatility_window,
            "c5_confirmation":  volatility_window,
        }

    def get_mercury_retrograde(self, date: Optional[datetime] = None) -> dict:
        if date is None:
            date = datetime.now(timezone.utc)
        date_str = date.strftime("%Y-%m-%d")
        for start, end in self.MERCURY_RETROGRADE:
            if start <= date_str <= end:
                return {
                    "active": True,
                    "period": f"{start} — {end}",
                    "warning": "Mercury retrograde: increased communication errors, "
                               "reversals, and false breakouts. Reduce position sizes.",
                }
        return {"active": False, "next": self._next_retrograde(date_str)}

    def _next_retrograde(self, date_str: str) -> str:
        for start, end in sorted(self.MERCURY_RETROGRADE):
            if start > date_str:
                return start
        return "N/A"

    def jubilee_cycle_check(self) -> dict:
        """
        Biblical Jubilee cycle: every 49/50 years. Approximate modern anchors:
        1966, 2015–16 → next window ~2064.
        Shemitah (7-year cycles): 2001, 2008, 2015, 2022, 2029...
        """
        now = datetime.now(timezone.utc).year
        shemitah_base = 2001
        years_into_cycle = (now - shemitah_base) % 7
        shemitah_year = now + (7 - years_into_cycle) if years_into_cycle else now
        in_shemitah = years_into_cycle == 0

        return {
            "current_year":      now,
            "shemitah_year":     shemitah_year,
            "years_into_cycle":  years_into_cycle,
            "in_shemitah_year":  in_shemitah,
            "note": "Shemitah years historically correlate with elevated volatility "
                    "and market corrections (2001, 2008, 2015, 2022). "
                    + ("SHEMITAH YEAR ACTIVE — elevated caution advised." if in_shemitah else
                       f"{7 - years_into_cycle} years until next Shemitah ({shemitah_year})."),
        }


# ── Supply & Demand Zone Detection ───────────────────────────────────────────

class SupplyDemandDetector:
    """
    Identifies institutional supply and demand zones from OHLCV data.
    A zone is valid if:
    - It caused a move of at least `min_move_pips` before being revisited
    - It has been tested ≤ `max_tests` times (more tests = weaker zone)
    """

    def detect_zones(
        self,
        candles: list[dict],
        min_move_pips: float = 50.0,
        pip_size: float = 0.0001,
        max_tests: int = 3,
    ) -> dict:
        if len(candles) < 10:
            return {"supply": [], "demand": [], "error": "Insufficient candle data"}

        supply_zones: list[dict] = []
        demand_zones: list[dict] = []

        # Simple detection: find strong impulse candles (body > 2× average body)
        bodies = [abs(c["close"] - c["open"]) for c in candles]
        avg_body = sum(bodies) / len(bodies) if bodies else 0.0001

        for i in range(2, len(candles) - 2):
            c = candles[i]
            body = abs(c["close"] - c["open"])
            if body < avg_body * 2:
                continue

            # Demand zone: strong bullish candle → zone is the base (prior consolidation)
            if c["close"] > c["open"]:
                zone_high = c["open"] + (c["close"] - c["open"]) * 0.33
                zone_low  = c["low"]
                move_pips = (c["high"] - zone_high) / pip_size
                if move_pips >= min_move_pips:
                    demand_zones.append({
                        "type":       "DEMAND",
                        "high":       round(zone_high, 5),
                        "low":        round(zone_low, 5),
                        "formed_at":  c["time"],
                        "move_pips":  round(move_pips, 1),
                        "tests":      self._count_tests(candles, i, zone_low, zone_high),
                        "strength":   "STRONG" if move_pips > min_move_pips * 2 else "MODERATE",
                    })

            # Supply zone: strong bearish candle
            else:
                zone_low  = c["open"] - (c["open"] - c["close"]) * 0.33
                zone_high = c["high"]
                move_pips = (zone_low - c["low"]) / pip_size
                if move_pips >= min_move_pips:
                    supply_zones.append({
                        "type":       "SUPPLY",
                        "high":       round(zone_high, 5),
                        "low":        round(zone_low, 5),
                        "formed_at":  c["time"],
                        "move_pips":  round(move_pips, 1),
                        "tests":      self._count_tests(candles, i, zone_low, zone_high),
                        "strength":   "STRONG" if move_pips > min_move_pips * 2 else "MODERATE",
                    })

        # Filter over-tested zones
        supply_zones = [z for z in supply_zones if z["tests"] <= max_tests]
        demand_zones = [z for z in demand_zones if z["tests"] <= max_tests]

        # Sort by recency
        supply_zones.sort(key=lambda z: z["formed_at"], reverse=True)
        demand_zones.sort(key=lambda z: z["formed_at"], reverse=True)

        return {
            "supply_zones": supply_zones[:5],
            "demand_zones": demand_zones[:5],
            "total_supply": len(supply_zones),
            "total_demand": len(demand_zones),
        }

    def _count_tests(
        self,
        candles: list[dict],
        formation_idx: int,
        zone_low: float,
        zone_high: float,
    ) -> int:
        count = 0
        for c in candles[formation_idx + 1:]:
            if c["low"] <= zone_high and c["high"] >= zone_low:
                count += 1
        return count


# ── Strategy Backtester ───────────────────────────────────────────────────────

class StrategyValidator:
    """
    Validates a strategy definition against the Phase 2 & 3 qualification criteria.
    Accepts a list of simulated trade outcomes (provided by the strategy definition).
    """

    def validate(self, trades: list[dict]) -> dict:
        """
        Each trade dict: {"outcome": "win"|"loss", "rr": float, "context": str}
        Returns pass/fail and full metrics.
        """
        n = len(trades)
        if n < 100:
            return {
                "passed": False,
                "reason": f"Insufficient trades: {n} (minimum 100 required)",
                "n": n,
            }

        wins   = [t for t in trades if t["outcome"] == "win"]
        losses = [t for t in trades if t["outcome"] == "loss"]

        win_rate = len(wins) / n
        avg_rr   = sum(t.get("rr", 0) for t in wins) / len(wins) if wins else 0.0

        # Max drawdown simulation
        equity = 1000.0
        peak   = 1000.0
        max_dd = 0.0
        for t in trades:
            if t["outcome"] == "win":
                equity += t.get("rr", 2) * 50    # assume $50 risk per trade
            else:
                equity -= 50
            peak = max(peak, equity)
            dd   = (peak - equity) / peak
            max_dd = max(max_dd, dd)

        # Max consecutive losses
        max_consec = 0
        cur_consec = 0
        for t in trades:
            if t["outcome"] == "loss":
                cur_consec += 1
                max_consec = max(max_consec, cur_consec)
            else:
                cur_consec = 0

        checks = {
            "win_rate_95pct":     win_rate >= 0.95,
            "avg_rr_2plus":       avg_rr >= 2.0,
            "max_consec_losses_3": max_consec <= 3,
            "max_drawdown_20pct": max_dd <= 0.20,
        }

        return {
            "passed":              all(checks.values()),
            "checks":              checks,
            "n_trades":            n,
            "win_rate":            round(win_rate, 4),
            "avg_rr":              round(avg_rr, 2),
            "max_consecutive_losses": max_consec,
            "max_drawdown_pct":    round(max_dd, 4),
            "total_wins":          len(wins),
            "total_losses":        len(losses),
            "verdict": (
                "STRATEGY VALIDATED — Approved for live deployment."
                if all(checks.values()) else
                "STRATEGY FAILED — Must not be deployed live. Archive and refine."
            ),
        }

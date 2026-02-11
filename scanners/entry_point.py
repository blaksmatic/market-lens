from typing import Optional

import pandas as pd

from scanners.base import BaseScanner, ScanResult, resample_ohlcv
from scanners.registry import register


@register
class EntryPointScanner(BaseScanner):
    """
    Detects actionable entry points on stocks in a confirmed uptrend.

    Looks for price approaching or touching daily MA10/MA20 with signs of
    holding support -- either the candle is near the MA, or a hammer/dragonfly
    doji (reversed T) formed with its wick testing the MA.

    Trend filter (must pass):
      - Daily MA10 > MA20 > MA50 (strong daily structure)
      - Weekly close > weekly MA20 (intermediate uptrend intact)
      - Full weekly alignment (close > w10 > w20) earns bonus points

    Entry signals (scored independently, best signal wins):
      1. APPROACHING: close within approach_pct% of MA10/MA20 (above or below)
      2. TOUCH: candle low reached MA10/MA20 but close held near/above it
      3. HAMMER: long lower wick tested MA10/MA20, close near candle high
         (the "reversed T" / dragonfly doji pattern)

    A hammer at the MA is the strongest signal because it shows sellers
    pushed price to the MA and got rejected. Price slightly below MA10
    is allowed since that IS the entry zone -- close must stay above MA20.
    """

    name = "entry_point"
    description = "Trend entry: approaching/touching MA10/20 or hammer at MA"

    def __init__(self):
        # Daily MAs
        self.d_fast = 10
        self.d_mid = 20
        self.d_slow = 50
        # Weekly MAs
        self.w_fast = 10
        self.w_mid = 20
        # Detection thresholds
        self.approach_pct = 3.0   # Close within X% above MA counts as approaching
        self.touch_pct = 0.5      # Low within X% of MA counts as touch
        self.lookback = 3         # Check last N candles for signals
        # Hammer detection
        self.wick_body_ratio = 2.0  # Lower wick must be >= N x body size
        self.upper_wick_max = 0.3   # Upper wick < 30% of total range

    def configure(self, **kwargs):
        int_keys = ("d_fast", "d_mid", "d_slow", "w_fast", "w_mid", "lookback")
        for key in int_keys:
            if key in kwargs:
                setattr(self, key, int(kwargs[key]))
        float_keys = ("approach_pct", "touch_pct", "wick_body_ratio", "upper_wick_max")
        for key in float_keys:
            if key in kwargs:
                setattr(self, key, float(kwargs[key]))

    def _detect_hammer(self, open_: float, high: float, low: float, close: float) -> bool:
        """
        Detect a hammer / dragonfly doji (reversed T) candle.

        Characteristics:
          - Long lower shadow (wick >= wick_body_ratio x body)
          - Small or no upper shadow (< upper_wick_max of total range)
          - Body is in the upper portion of the candle
        """
        total_range = high - low
        if total_range <= 0:
            return False

        body = abs(close - open_)
        body_top = max(close, open_)
        body_bottom = min(close, open_)
        lower_wick = body_bottom - low
        upper_wick = high - body_top

        # Lower wick must be significant relative to body
        # For doji (tiny body), just check lower wick is majority of range
        if body < total_range * 0.05:
            # Doji-like: lower wick should be >60% of range, upper wick small
            return (lower_wick > total_range * 0.6
                    and upper_wick < total_range * self.upper_wick_max)

        # Standard hammer
        return (lower_wick >= body * self.wick_body_ratio
                and upper_wick < total_range * self.upper_wick_max)

    def scan(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
    ) -> Optional[ScanResult]:
        min_rows = self.d_slow + 20
        if len(ohlcv) < min_rows:
            return None

        # --- Weekly trend filter ---
        weekly = resample_ohlcv(ohlcv, "W")
        if len(weekly) < self.w_mid + 2:
            return None

        w_close = weekly["Close"]
        w_mf = w_close.rolling(self.w_fast).mean().iloc[-1]
        w_mm = w_close.rolling(self.w_mid).mean().iloc[-1]
        w_last = w_close.iloc[-1]

        # Weekly: price must be above weekly MA20 (intermediate trend intact)
        if not (w_last > w_mm):
            return None
        weekly_full_align = w_last > w_mf > w_mm

        # --- Daily trend filter ---
        close = ohlcv["Close"]
        d_mf = close.rolling(self.d_fast).mean()
        d_mm = close.rolling(self.d_mid).mean()
        d_ms = close.rolling(self.d_slow).mean()

        mf_val = d_mf.iloc[-1]
        mm_val = d_mm.iloc[-1]
        ms_val = d_ms.iloc[-1]

        # Daily MAs must be aligned: fast > mid > slow
        if not (mf_val > mm_val > ms_val):
            return None

        # Latest close must be above MA20 (the floor)
        # Being below MA10 is fine -- that's the entry zone
        if close.iloc[-1] < mm_val:
            return None

        # --- Scan last N candles for entry signals ---
        best_signal = None
        best_score = 0
        best_details = {}

        for i in range(-self.lookback, 0):
            idx = len(ohlcv) + i
            if idx < 0:
                continue

            ago = -i - 1  # 0 = most recent, 1 = yesterday, 2 = two days ago...
            # Recency multiplier: ago=0 → 1.0, ago=1 → 0.7, ago=2 → 0.4, ...
            recency = max(0.0, 1.0 - ago * 0.3)

            row = ohlcv.iloc[idx]
            c = row["Close"]
            o = row["Open"]
            h = row["High"]
            l = row["Low"]  # noqa: E741
            ma10 = d_mf.iloc[idx]
            ma20 = d_mm.iloc[idx]

            for ma_val, ma_label in [(ma10, f"MA{self.d_fast}"), (ma20, f"MA{self.d_mid}")]:
                close_dist_pct = (c - ma_val) / ma_val * 100
                low_dist_pct = (l - ma_val) / ma_val * 100

                # Close must be above MA20 (the floor); below MA10 is ok (entry zone)
                if ma_label == f"MA{self.d_mid}" and c < ma_val:
                    continue

                # --- Signal 3: HAMMER at MA (strongest) ---
                is_hammer = self._detect_hammer(o, h, l, c)
                low_near_ma = abs(low_dist_pct) <= self.touch_pct or low_dist_pct <= 0

                if is_hammer and low_near_ma:
                    proximity = max(0, (1 - abs(low_dist_pct) / max(self.touch_pct, 0.01))) * 20
                    s = (40 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "HAMMER"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2),
                            "candle_ago": ago,
                        }
                    continue  # Don't also count as touch

                # --- Signal 2: TOUCH (low reached MA, close held near it) ---
                if low_near_ma:
                    proximity = max(0, (1 - abs(low_dist_pct) / max(self.touch_pct, 0.01))) * 15
                    s = (25 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "TOUCH"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2),
                            "candle_ago": ago,
                        }
                    continue

                # --- Signal 1: APPROACHING (close near MA, above or slightly below) ---
                if abs(close_dist_pct) <= self.approach_pct:
                    proximity = max(0, (1 - abs(close_dist_pct) / self.approach_pct)) * 15
                    s = (10 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "APPROACHING"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2),
                            "candle_ago": ago,
                        }

        if best_signal is None:
            return None

        # --- Resistance / ATH analysis ---
        # ATH from all available history; also check 52-week high
        ath = ohlcv["High"].max()
        high_52w = ohlcv["High"].iloc[-252:].max() if len(ohlcv) >= 252 else ath
        latest_close = close.iloc[-1]

        pct_from_ath = (ath - latest_close) / ath * 100
        pct_from_52w = (high_52w - latest_close) / high_52w * 100

        # Near ATH = no overhead resistance, clear sky
        # Within 3% of ATH: full bonus (20pts)
        # Within 5%: good bonus (15pts)
        # Within 10%: moderate (8pts)
        # Beyond 10%: penalty -- lots of resistance above
        if pct_from_ath <= 3:
            resistance_bonus = 20
        elif pct_from_ath <= 5:
            resistance_bonus = 15
        elif pct_from_ath <= 10:
            resistance_bonus = 8
        else:
            resistance_bonus = 0

        # Extra: if recent 20-day high is also near ATH, momentum is strong
        recent_high = ohlcv["High"].iloc[-20:].max()
        pct_recent_from_ath = (ath - recent_high) / ath * 100
        if pct_recent_from_ath <= 2:
            resistance_bonus += 5  # Recently tested ATH zone

        best_score += resistance_bonus

        # --- Trend strength bonus ---
        # Daily MA spread
        d_spread_pct = (mf_val - ms_val) / ms_val * 100
        trend_bonus = min(15, d_spread_pct * 3)
        best_score += trend_bonus

        # Weekly alignment bonus (close > w10 > w20 = strong weekly)
        w_spread_pct = (w_mf - w_mm) / w_mm * 100
        if weekly_full_align:
            weekly_bonus = min(15, w_spread_pct * 2 + 5)
        else:
            weekly_bonus = min(5, max(0, w_spread_pct))
        best_score += weekly_bonus

        # Latest candle green bonus
        latest_green = close.iloc[-1] > ohlcv["Open"].iloc[-1]
        if latest_green:
            best_score += 5

        score = min(100.0, best_score)

        if score >= 65:
            signal = "STRONG_BUY"
        elif score >= 40:
            signal = "BUY"
        else:
            signal = "WATCH"

        # Compact entry label: "HMR@M10(0d)"
        _sig_short = {"HAMMER": "HMR", "TOUCH": "TCH", "APPROACHING": "APR"}
        ma_label = best_details.get("ma", "").replace("MA", "M")
        ago_val = best_details.get("candle_ago", 0)
        entry_label = f"{_sig_short.get(best_signal, best_signal)}@{ma_label}({ago_val}d)"

        return ScanResult(
            ticker=ticker,
            score=round(score, 1),
            signal=signal,
            details={
                "entry": entry_label,
                "dist%": round(best_details.get("close_dist_%", 0), 1),
                "ath%": round(pct_from_ath, 1),
                "wk": "Y" if weekly_full_align else "",
                "cap$B": round(fundamentals.get("marketCap", 0) / 1e9),
            },
        )

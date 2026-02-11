from typing import Optional

import pandas as pd

from scanners.base import BaseScanner, ScanResult, resample_ohlcv
from scanners.registry import register


@register
class StrongPullbackScanner(BaseScanner):
    """
    Finds very strong tickers that pull back to daily MA10/MA20 and bounce,
    confirmed by bullish weekly MA structure.

    Weekly filter:
      - Weekly close > weekly MA10 > weekly MA20 > weekly MA40
      - Ensures the stock is in a strong intermediate uptrend

    Daily trigger:
      - Daily MA10 > MA20 > MA50 (strong daily trend)
      - Price dipped to touch or pierce MA10/MA20 in the last N days
        (low <= MA within touch_pct%)
      - Price has bounced: latest close is back above the MA it touched
      - Close > Open on the latest bar (green candle = buying pressure)

    Scoring:
      - Tighter touch = higher score
      - Weekly MA spread (strength of weekly trend)
      - Bounce strength (how far above the touched MA)
    """

    name = "strong_pullback"
    description = "Strong weekly trend + daily MA10/20 touch & bounce"

    def __init__(self):
        # Daily MAs
        self.d_fast = 10
        self.d_mid = 20
        self.d_slow = 50
        # Weekly MAs
        self.w_fast = 10
        self.w_mid = 20
        self.w_slow = 40
        # Pullback detection
        self.lookback_days = 5  # How many recent days to check for a touch
        self.touch_pct = 1.0  # Low must come within X% of MA to count as touch
        # Minimum daily alignment duration
        self.min_align_days = 5

    def configure(self, **kwargs):
        int_keys = (
            "d_fast", "d_mid", "d_slow",
            "w_fast", "w_mid", "w_slow",
            "lookback_days", "min_align_days",
        )
        for key in int_keys:
            if key in kwargs:
                setattr(self, key, int(kwargs[key]))
        if "touch_pct" in kwargs:
            self.touch_pct = float(kwargs["touch_pct"])

    def scan(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
    ) -> Optional[ScanResult]:
        # Need enough daily history for the slowest daily MA
        min_daily = self.d_slow + self.lookback_days + 10
        if len(ohlcv) < min_daily:
            return None

        # --- Weekly filter ---
        weekly = resample_ohlcv(ohlcv, "W")
        if len(weekly) < self.w_slow + 2:
            return None

        w_close = weekly["Close"]
        w_ma_fast = w_close.rolling(self.w_fast).mean()
        w_ma_mid = w_close.rolling(self.w_mid).mean()
        w_ma_slow = w_close.rolling(self.w_slow).mean()

        wf = w_ma_fast.iloc[-1]
        wm = w_ma_mid.iloc[-1]
        ws = w_ma_slow.iloc[-1]
        w_latest = w_close.iloc[-1]

        # Weekly must be bullish: close > fast > mid > slow
        if not (w_latest > wf > wm > ws):
            return None

        # --- Daily alignment ---
        close = ohlcv["Close"]
        low = ohlcv["Low"]
        d_ma_fast = close.rolling(self.d_fast).mean()
        d_ma_mid = close.rolling(self.d_mid).mean()
        d_ma_slow = close.rolling(self.d_slow).mean()

        df_val = d_ma_fast.iloc[-1]
        dm_val = d_ma_mid.iloc[-1]
        ds_val = d_ma_slow.iloc[-1]

        # Daily MAs must be aligned: fast > mid > slow
        if not (df_val > dm_val > ds_val):
            return None

        # Alignment must have held for min_align_days
        tail_f = d_ma_fast.tail(self.min_align_days)
        tail_m = d_ma_mid.tail(self.min_align_days)
        tail_s = d_ma_slow.tail(self.min_align_days)
        align_days = int(((tail_f > tail_m) & (tail_m > tail_s)).sum())
        if align_days < self.min_align_days:
            return None

        # --- Pullback touch detection ---
        # Look at last N days: did the low come within touch_pct% of MA10 or MA20?
        window = slice(-self.lookback_days, None)
        recent_low = low.iloc[window]
        recent_close = close.iloc[window]
        recent_ma10 = d_ma_fast.iloc[window]
        recent_ma20 = d_ma_mid.iloc[window]

        # Distance of low from each MA (negative = pierced below)
        dist_to_10 = (recent_low - recent_ma10) / recent_ma10 * 100
        dist_to_20 = (recent_low - recent_ma20) / recent_ma20 * 100

        # A "touch" means low came within touch_pct% of the MA (above or below)
        touched_10 = (dist_to_10.abs() <= self.touch_pct).any() or (dist_to_10 <= 0).any()
        touched_20 = (dist_to_20.abs() <= self.touch_pct).any() or (dist_to_20 <= 0).any()

        if not (touched_10 or touched_20):
            return None

        # Determine which MA was touched (prefer MA10 as tighter = stronger)
        if touched_10:
            touch_ma_label = f"MA{self.d_fast}"
            touch_ma_val = df_val
            best_touch_dist = dist_to_10.abs().min()
        else:
            touch_ma_label = f"MA{self.d_mid}"
            touch_ma_val = dm_val
            best_touch_dist = dist_to_20.abs().min()

        # --- Bounce confirmation ---
        latest_close = close.iloc[-1]
        latest_open = ohlcv["Open"].iloc[-1]

        # Price must be back above the touched MA
        if latest_close < touch_ma_val:
            return None

        # Green candle on latest bar (buying pressure)
        green_candle = latest_close > latest_open

        # --- Scoring ---
        # Touch tightness: 0% distance = 30pts, touch_pct% = 0pts
        touch_score = (1 - best_touch_dist / self.touch_pct) * 30

        # Bounce strength: how far above the touched MA (cap at 5%)
        bounce_pct = (latest_close - touch_ma_val) / touch_ma_val * 100
        bounce_score = min(20, bounce_pct * 10)

        # Weekly trend strength: spread between weekly fast and slow MA
        w_spread_pct = (wf - ws) / ws * 100
        weekly_score = min(30, w_spread_pct * 3)

        # Green candle bonus
        candle_bonus = 10 if green_candle else 0

        # MA10 touch is stronger than MA20 touch
        ma10_bonus = 10 if touched_10 else 0

        score = touch_score + bounce_score + weekly_score + candle_bonus + ma10_bonus

        signal = "STRONG_BUY" if score >= 70 else "BUY" if score >= 45 else "WATCH"

        return ScanResult(
            ticker=ticker,
            score=round(score, 1),
            signal=signal,
            details={
                "close": round(latest_close, 2),
                "touch": touch_ma_label,
                "touch_dist_%": round(best_touch_dist, 2),
                "bounce_%": round(bounce_pct, 2),
                "green": "Y" if green_candle else "N",
                f"d_ma{self.d_fast}": round(df_val, 2),
                f"d_ma{self.d_mid}": round(dm_val, 2),
                f"w_spread_%": round(w_spread_pct, 1),
                "sector": fundamentals.get("sector", "N/A"),
                "mkt_cap_B": round(fundamentals.get("marketCap", 0) / 1e9),
            },
        )

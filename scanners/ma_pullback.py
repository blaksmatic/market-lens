from typing import Optional

import pandas as pd

from scanners.base import BaseScanner, ScanResult
from scanners.registry import register


@register
class MAPullbackScanner(BaseScanner):
    """
    Multi-timeframe MA alignment with pullback to short MA.

    Detects stocks where moving averages are aligned bullishly
    (short > medium > long) and price has pulled back near the short MA,
    presenting a potential entry point.
    """

    name = "ma_pullback"
    description = "Multi-timeframe MA alignment with pullback to short MA"

    def __init__(self):
        self.ma_short = 5
        self.ma_medium = 10
        self.ma_long = 20
        self.ma_trend = 50
        self.pullback_pct = 2.0  # Price within X% of short MA
        self.min_trend_days = 10  # MAs must be aligned for at least N days

    def configure(self, **kwargs):
        for key in ("ma_short", "ma_medium", "ma_long", "ma_trend", "min_trend_days"):
            if key in kwargs:
                setattr(self, key, int(kwargs[key]))
        if "pullback_pct" in kwargs:
            self.pullback_pct = float(kwargs["pullback_pct"])

    def scan(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        fundamentals: pd.Series,
    ) -> Optional[ScanResult]:
        min_period = max(self.ma_long, self.ma_trend)
        if len(ohlcv) < min_period + self.min_trend_days:
            return None

        close = ohlcv["Close"]
        sma_short = close.rolling(self.ma_short).mean()
        sma_medium = close.rolling(self.ma_medium).mean()
        sma_long = close.rolling(self.ma_long).mean()
        sma_trend = close.rolling(self.ma_trend).mean()

        latest = close.iloc[-1]
        s = sma_short.iloc[-1]
        m = sma_medium.iloc[-1]
        l = sma_long.iloc[-1]  # noqa: E741
        t = sma_trend.iloc[-1]

        # Check 1: Bullish MA alignment (MA5 > MA10 > MA20)
        if not (s > m > l):
            return None

        # Check 2: Alignment has persisted for min_trend_days
        tail_s = sma_short.tail(self.min_trend_days)
        tail_m = sma_medium.tail(self.min_trend_days)
        tail_l = sma_long.tail(self.min_trend_days)
        alignment_days = int(((tail_s > tail_m) & (tail_m > tail_l)).sum())
        if alignment_days < self.min_trend_days:
            return None

        # Check 3: Price pulled back near the short MA
        distance_pct = abs(latest - s) / s * 100
        if distance_pct > self.pullback_pct:
            return None

        # MA50 alignment bonus (MA20 > MA50)
        ma50_aligned = l > t

        # Score: tighter pullback + stronger alignment = higher score
        distance_score = (1 - distance_pct / self.pullback_pct) * 50
        spread_pct = (s - l) / l * 100
        spread_score = min(50, spread_pct * 5)
        ma50_bonus = 15 if ma50_aligned else 0
        score = distance_score + spread_score + ma50_bonus

        signal = "STRONG_BUY" if score >= 70 else "BUY" if score >= 40 else "WATCH"

        return ScanResult(
            ticker=ticker,
            score=round(score, 1),
            signal=signal,
            details={
                "close": round(latest, 2),
                f"sma_{self.ma_short}": round(s, 2),
                f"sma_{self.ma_medium}": round(m, 2),
                f"sma_{self.ma_long}": round(l, 2),
                "ma50": "Y" if ma50_aligned else "",
                "pullback_%": round(distance_pct, 2),
                "spread_%": round(spread_pct, 2),
                "align_days": alignment_days,
                "sector": fundamentals.get("sector", "N/A"),
                "mkt_cap_B": round(fundamentals.get("marketCap", 0) / 1e9),
            },
        )

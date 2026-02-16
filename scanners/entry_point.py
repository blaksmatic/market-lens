from typing import Optional

import pandas as pd

from scanners.base import BaseScanner, EntrySignal, ExitSignal, ScanResult, resample_ohlcv
from scanners.registry import register


@register
class EntryPointScanner(BaseScanner):
    """
    Detects actionable entry points on stocks in a confirmed uptrend.

    Looks for price approaching or touching daily MA10/MA20 with signs of
    holding support -- either the candle is near the MA, or a hammer/dragonfly
    doji (reversed T) formed with its wick testing the MA.

    Trend filter (must pass):
      - Daily MA5 > MA10 > MA20 (short-term trend intact)
      - Weekly close > weekly MA20 (intermediate uptrend intact)
      - MA20 > MA50 alignment earns +15 bonus points
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
        self.d_xfast = 5
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
        int_keys = ("d_xfast", "d_fast", "d_mid", "d_slow", "w_fast", "w_mid", "lookback")
        for key in int_keys:
            if key in kwargs:
                setattr(self, key, int(kwargs[key]))
        float_keys = ("approach_pct", "touch_pct", "wick_body_ratio", "upper_wick_max")
        for key in float_keys:
            if key in kwargs:
                setattr(self, key, float(kwargs[key]))

    # ------------------------------------------------------------------
    # Shared indicator & signal logic (used by both scan and simulation)
    # ------------------------------------------------------------------

    def _compute_indicators(self, ohlcv: pd.DataFrame) -> Optional[dict]:
        """Compute all technical indicators on the dataset. Returns None if insufficient data."""
        if len(ohlcv) < self.d_slow + 20:
            return None

        close = ohlcv["Close"]
        ind = {
            "d_mxf": close.rolling(self.d_xfast).mean(),
            "d_mf": close.rolling(self.d_fast).mean(),
            "d_mm": close.rolling(self.d_mid).mean(),
            "d_ms": close.rolling(self.d_slow).mean(),
            "ath": ohlcv["High"].expanding().max(),
            "vol_avg": ohlcv["Volume"].rolling(20).mean(),
        }

        weekly = resample_ohlcv(ohlcv, "W")
        if len(weekly) < self.w_mid + 2:
            return None

        w_close = weekly["Close"]
        ind["w_close"] = w_close.reindex(ohlcv.index, method="ffill")
        ind["w_mf"] = w_close.rolling(self.w_fast).mean().reindex(ohlcv.index, method="ffill")
        ind["w_mm"] = w_close.rolling(self.w_mid).mean().reindex(ohlcv.index, method="ffill")

        return ind

    def _check_entry_at(self, idx: int, ohlcv: pd.DataFrame, ind: dict) -> Optional[dict]:
        """
        Check entry conditions at a specific index using precomputed indicators.
        Returns dict with score/signal/details or None.
        """
        min_idx = self.d_slow + 20
        if idx < min_idx:
            return None

        c = ohlcv["Close"].iloc[idx]

        # Weekly filter
        w_last = ind["w_close"].iloc[idx]
        w_mf = ind["w_mf"].iloc[idx]
        w_mm = ind["w_mm"].iloc[idx]
        if pd.isna(w_mm) or not (w_last > w_mm):
            return None
        weekly_full_align = w_last > w_mf > w_mm

        # Daily filter
        mxf_val = ind["d_mxf"].iloc[idx]
        mf_val = ind["d_mf"].iloc[idx]
        mm_val = ind["d_mm"].iloc[idx]
        ms_val = ind["d_ms"].iloc[idx]
        if pd.isna(mxf_val) or not (mxf_val > mf_val > mm_val):
            return None
        if c < mm_val:
            return None

        ma50_aligned = mm_val > ms_val

        # --- Signal detection over lookback window ---
        best_signal = None
        best_score = 0
        best_details = {}

        for j in range(max(min_idx, idx - self.lookback + 1), idx + 1):
            ago = idx - j
            recency = max(0.0, 1.0 - ago * 0.3)

            c_j = ohlcv["Close"].iloc[j]
            o_j = ohlcv["Open"].iloc[j]
            h_j = ohlcv["High"].iloc[j]
            l_j = ohlcv["Low"].iloc[j]
            ma10_j = ind["d_mf"].iloc[j]
            ma20_j = ind["d_mm"].iloc[j]

            for ma_val, ma_label in [(ma10_j, f"MA{self.d_fast}"), (ma20_j, f"MA{self.d_mid}")]:
                close_dist_pct = (c_j - ma_val) / ma_val * 100
                low_dist_pct = (l_j - ma_val) / ma_val * 100

                if ma_label == f"MA{self.d_mid}" and c_j < ma_val:
                    continue

                is_hammer = self._detect_hammer(o_j, h_j, l_j, c_j)
                low_near_ma = abs(low_dist_pct) <= self.touch_pct or low_dist_pct <= 0

                # HAMMER (strongest)
                if is_hammer and low_near_ma:
                    proximity = max(0, (1 - abs(low_dist_pct) / max(self.touch_pct, 0.01))) * 20
                    s = (40 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "HAMMER"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2), "candle_ago": ago,
                        }
                    continue

                # TOUCH
                if low_near_ma:
                    proximity = max(0, (1 - abs(low_dist_pct) / max(self.touch_pct, 0.01))) * 15
                    s = (25 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "TOUCH"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2), "candle_ago": ago,
                        }
                    continue

                # APPROACHING
                if abs(close_dist_pct) <= self.approach_pct:
                    proximity = max(0, (1 - abs(close_dist_pct) / self.approach_pct)) * 15
                    s = (10 + proximity) * recency
                    if s > best_score:
                        best_score = s
                        best_signal = "APPROACHING"
                        best_details = {
                            "ma": ma_label, "low_dist_%": round(abs(low_dist_pct), 2),
                            "close_dist_%": round(close_dist_pct, 2), "candle_ago": ago,
                        }

        if best_signal is None:
            return None

        # --- Resistance / ATH bonus ---
        ath = ind["ath"].iloc[idx]
        pct_from_ath = (ath - c) / ath * 100
        if pct_from_ath <= 3:
            best_score += 20
        elif pct_from_ath <= 5:
            best_score += 15
        elif pct_from_ath <= 10:
            best_score += 8

        recent_high = ohlcv["High"].iloc[max(0, idx - 20):idx + 1].max()
        if (ath - recent_high) / ath * 100 <= 2:
            best_score += 5

        # --- Bonus scores ---
        if ma50_aligned:
            best_score += 15
        d_spread_pct = (mxf_val - mm_val) / mm_val * 100
        best_score += min(15, d_spread_pct * 3)
        w_spread_pct = (w_mf - w_mm) / w_mm * 100
        if weekly_full_align:
            best_score += min(15, w_spread_pct * 2 + 5)
        else:
            best_score += min(5, max(0, w_spread_pct))
        if c > ohlcv["Open"].iloc[idx]:
            best_score += 5

        score = min(100.0, best_score)
        signal = "STRONG_BUY" if score >= 65 else ("BUY" if score >= 40 else "WATCH")

        _sig_short = {"HAMMER": "HMR", "TOUCH": "TCH", "APPROACHING": "APR"}
        ml = best_details.get("ma", "").replace("MA", "M")
        ag = best_details.get("candle_ago", 0)
        entry_label = f"{_sig_short.get(best_signal, best_signal)}@{ml}({ag}d)"

        return {
            "score": round(score, 1),
            "signal": signal,
            "entry_label": entry_label,
            "best_details": best_details,
            "pct_from_ath": pct_from_ath,
            "weekly_full_align": weekly_full_align,
        }

    def _detect_hammer(self, open_: float, high: float, low: float, close: float) -> bool:
        """Detect a hammer / dragonfly doji (reversed T) candle."""
        total_range = high - low
        if total_range <= 0:
            return False

        body = abs(close - open_)
        body_top = max(close, open_)
        body_bottom = min(close, open_)
        lower_wick = body_bottom - low
        upper_wick = high - body_top

        if body < total_range * 0.05:
            return (lower_wick > total_range * 0.6
                    and upper_wick < total_range * self.upper_wick_max)

        return (lower_wick >= body * self.wick_body_ratio
                and upper_wick < total_range * self.upper_wick_max)

    # ------------------------------------------------------------------
    # scan() — single point-in-time evaluation (analyze command)
    # ------------------------------------------------------------------

    def scan(self, ticker, ohlcv, fundamentals) -> Optional[ScanResult]:
        ind = self._compute_indicators(ohlcv)
        if ind is None:
            return None

        result = self._check_entry_at(len(ohlcv) - 1, ohlcv, ind)
        if result is None:
            return None

        return ScanResult(
            ticker=ticker,
            score=result["score"],
            signal=result["signal"],
            details={
                "entry": result["entry_label"],
                "dist%": round(result["best_details"].get("close_dist_%", 0), 1),
                "ath%": round(result["pct_from_ath"], 1),
                "wk": "Y" if result["weekly_full_align"] else "",
                "cap$B": round(fundamentals.get("marketCap", 0) / 1e9),
            },
        )

    # ------------------------------------------------------------------
    # Simulation support — precompute once, O(1) lookups per day
    # ------------------------------------------------------------------

    def prepare_simulation(self, ticker, ohlcv, fundamentals):
        self._sim_ticker = ticker
        self._sim_entries = {}

        ind = self._compute_indicators(ohlcv)
        if ind is None:
            self._sim_ind = None
            return

        self._sim_ind = ind

        for i in range(self.d_slow + 20, len(ohlcv)):
            result = self._check_entry_at(i, ohlcv, ind)
            if result is not None:
                date = ohlcv.index[i]
                self._sim_entries[date] = EntrySignal(
                    date=date,
                    price=ohlcv["Close"].iloc[i],
                    reason=result["signal"],
                    metadata={"entry": result["entry_label"], "score": result["score"]},
                )

    def check_entry_signal(self, ticker, ohlcv, fundamentals, as_of_date):
        if hasattr(self, "_sim_ticker") and self._sim_ticker == ticker:
            return self._sim_entries.get(as_of_date)
        return super().check_entry_signal(ticker, ohlcv, fundamentals, as_of_date)

    def check_exit_signal(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        entry_signal: EntrySignal,
        current_date: pd.Timestamp,
    ) -> Optional[ExitSignal]:
        """
        Entry Point exit rules:
        1. Stop-loss: 10% below entry
        2. Profit target: 15% above entry
        3. MA20 breakdown: close below MA20 for 3 consecutive days
        4. Sharp drop: close > 5% below MA20
        5. Volume breakdown: 2x average volume + close below MA20
        6. Time exit: 30 days max hold
        """
        ohlcv_slice = ohlcv.loc[:current_date]
        if len(ohlcv_slice) < self.d_mid + 5:
            return None

        current_close = ohlcv_slice["Close"].iloc[-1]
        entry_price = entry_signal.price

        # Use precomputed indicators if available
        use_cache = (
            hasattr(self, "_sim_ind")
            and self._sim_ind is not None
            and self._sim_ticker == ticker
        )

        if use_cache:
            ind = self._sim_ind
            try:
                idx = ind["d_mm"].index.get_loc(current_date)
            except KeyError:
                return None
            current_ma20 = ind["d_mm"].iloc[idx]
            if pd.isna(current_ma20):
                return None
        else:
            close_s = ohlcv_slice["Close"]
            ma20_series = close_s.rolling(self.d_mid).mean()
            current_ma20 = ma20_series.iloc[-1]

        # 1. Stop-loss
        if current_close < entry_price * 0.90:
            return ExitSignal(date=current_date, price=current_close, reason="STOP_LOSS")

        # 2. Profit target
        if current_close > entry_price * 1.15:
            return ExitSignal(date=current_date, price=current_close, reason="PROFIT_TARGET")

        # 3. MA20 breakdown: 3 consecutive closes below MA20
        if use_cache:
            if idx >= 2:
                last_3_close = ohlcv_slice["Close"].iloc[-3:]
                last_3_ma20 = ind["d_mm"].iloc[idx - 2:idx + 1]
                if len(last_3_ma20) == 3 and all(last_3_close.values < last_3_ma20.values):
                    return ExitSignal(date=current_date, price=current_close, reason="MA20_BREAKDOWN")
        else:
            if len(close_s) >= 3 and len(ma20_series.dropna()) >= 3:
                if all(close_s.iloc[-3:] < ma20_series.iloc[-3:]):
                    return ExitSignal(date=current_date, price=current_close, reason="MA20_BREAKDOWN")

        # 4. Sharp drop: > 5% below MA20
        if current_ma20 > 0:
            dist_pct = (current_close - current_ma20) / current_ma20 * 100
            if dist_pct < -5.0:
                return ExitSignal(date=current_date, price=current_close, reason="SHARP_DROP")

        # 5. Volume breakdown: 2x avg volume + close below MA20
        if current_close < current_ma20:
            if use_cache:
                avg_vol = ind["vol_avg"].iloc[idx]
                cur_vol = ohlcv.loc[current_date, "Volume"]
            else:
                volume = ohlcv_slice["Volume"]
                avg_vol = volume.iloc[-20:].mean() if len(volume) >= 20 else 0
                cur_vol = volume.iloc[-1]
            if avg_vol > 0 and cur_vol > avg_vol * 2:
                return ExitSignal(date=current_date, price=current_close, reason="VOLUME_BREAKDOWN")

        # 6. Time exit
        days_held = (current_date - entry_signal.date).days
        if days_held >= 30:
            return ExitSignal(date=current_date, price=current_close, reason="TIME_EXIT")

        return None

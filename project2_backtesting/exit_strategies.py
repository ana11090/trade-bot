"""
EXIT STRATEGIES — Pluggable exit strategy implementations.
Each strategy decides when to close a position based on price action.
Used by the strategy backtester to test different exit approaches.
"""
import numpy as np


class ExitStrategy:
    """Base class for all exit strategies."""
    name = "base"

    def __init__(self, pip_size=0.01, **params):
        self.pip_size = pip_size
        self.params   = params

    def on_new_candle(self, candle, position_info):
        """
        Called for each new candle while a position is open.

        Args:
            candle: dict with keys: timestamp, open, high, low, close, volume
                    AND indicator values (e.g. atr_14, rsi_14, etc.)
            position_info: dict with keys:
                entry_price, entry_time, direction ("BUY"/"SELL"),
                highest_since_entry, lowest_since_entry,
                candles_held, minutes_held, current_pnl_pips

        Returns:
            None if position should stay open
            dict with {"exit_price": float, "reason": str} if position should close
        """
        raise NotImplementedError

    def describe(self):
        """Return human-readable description of this strategy."""
        return f"{self.name}: {self.params}"

    @staticmethod
    def _resolve_sl_tp_priority(candle, sl_price, tp_price, direction):
        """
        When both SL and TP could be hit in one candle, determine which
        was hit first based on candle open direction.

        Returns: "SL", "TP", or None if neither was hit.
        """
        candle_open = float(candle["open"])
        candle_high = float(candle["high"])
        candle_low  = float(candle["low"])

        if direction == "BUY":
            sl_hit = candle_low  <= sl_price
            tp_hit = candle_high >= tp_price
            if sl_hit and tp_hit:
                return "SL" if abs(candle_open - sl_price) < abs(candle_open - tp_price) else "TP"
            if sl_hit: return "SL"
            if tp_hit: return "TP"
        else:  # SELL
            sl_hit = candle_high >= sl_price
            tp_hit = candle_low  <= tp_price
            if sl_hit and tp_hit:
                return "SL" if abs(candle_open - sl_price) < abs(candle_open - tp_price) else "TP"
            if sl_hit: return "SL"
            if tp_hit: return "TP"
        return None


class FixedSLTP(ExitStrategy):
    """Fixed stop loss and take profit in pips."""
    name = "Fixed SL/TP"

    def __init__(self, sl_pips=150, tp_pips=300, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips, tp_pips=tp_pips)
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        if direction == "BUY":
            sl_price = entry - self.sl_pips * self.pip_size
            tp_price = entry + self.tp_pips * self.pip_size
        else:  # SELL
            sl_price = entry + self.sl_pips * self.pip_size
            tp_price = entry - self.tp_pips * self.pip_size

        result = self._resolve_sl_tp_priority(candle, sl_price, tp_price, direction)
        if result == "SL":
            return {"exit_price": sl_price, "reason": "STOP_LOSS"}
        if result == "TP":
            return {"exit_price": tp_price, "reason": "TAKE_PROFIT"}
        return None

    def describe(self):
        return f"Fixed SL {self.sl_pips} pips / TP {self.tp_pips} pips"


class TrailingStop(ExitStrategy):
    """Fixed SL with trailing stop that activates after price moves in profit."""
    name = "Trailing Stop"

    def __init__(self, sl_pips=150, activation_pips=50, trail_distance_pips=100, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         activation_pips=activation_pips,
                         trail_distance_pips=trail_distance_pips)
        self.sl_pips             = sl_pips
        self.activation_pips     = activation_pips
        self.trail_distance_pips = trail_distance_pips

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        highest   = pos["highest_since_entry"]
        lowest    = pos["lowest_since_entry"]

        if direction == "BUY":
            fixed_sl    = entry - self.sl_pips * self.pip_size
            profit_pips = (highest - entry) / self.pip_size
            if profit_pips >= self.activation_pips:
                trail_sl     = highest - self.trail_distance_pips * self.pip_size
                effective_sl = max(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["low"] <= effective_sl:
                reason = "TRAILING_STOP" if effective_sl > fixed_sl else "STOP_LOSS"
                return {"exit_price": effective_sl, "reason": reason}
        else:  # SELL
            fixed_sl    = entry + self.sl_pips * self.pip_size
            profit_pips = (entry - lowest) / self.pip_size
            if profit_pips >= self.activation_pips:
                trail_sl     = lowest + self.trail_distance_pips * self.pip_size
                effective_sl = min(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["high"] >= effective_sl:
                reason = "TRAILING_STOP" if effective_sl < fixed_sl else "STOP_LOSS"
                return {"exit_price": effective_sl, "reason": reason}

        return None

    def describe(self):
        return (f"SL {self.sl_pips} pips, trail after +{self.activation_pips} pips, "
                f"trail distance {self.trail_distance_pips} pips")


class ATRBased(ExitStrategy):
    """SL and TP based on ATR (adapts to volatility)."""
    name = "ATR-Based"

    def __init__(self, sl_atr_mult=1.5, tp_atr_mult=3.0, atr_column="H1_atr_14"):
        super().__init__(sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.atr_column  = atr_column
        self._entry_atr  = None

    def on_entry(self, candle):
        """Called when position is opened — capture ATR at entry."""
        self._entry_atr = candle.get(self.atr_column, 5.0)

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        atr       = self._entry_atr or 5.0

        sl_distance = atr * self.sl_atr_mult
        tp_distance = atr * self.tp_atr_mult

        if direction == "BUY":
            sl_price = entry - sl_distance
            tp_price = entry + tp_distance
        else:
            sl_price = entry + sl_distance
            tp_price = entry - tp_distance

        result = self._resolve_sl_tp_priority(candle, sl_price, tp_price, direction)
        if result == "SL":
            return {"exit_price": sl_price, "reason": "ATR_STOP_LOSS"}
        if result == "TP":
            return {"exit_price": tp_price, "reason": "ATR_TAKE_PROFIT"}
        return None

    def describe(self):
        return f"SL {self.sl_atr_mult}xATR, TP {self.tp_atr_mult}xATR"


class TimeBased(ExitStrategy):
    """Fixed SL with time-based forced exit."""
    name = "Time-Based"

    def __init__(self, sl_pips=150, max_candles=6, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips, max_candles=max_candles)
        self.sl_pips    = sl_pips
        self.max_candles = max_candles

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        if direction == "BUY":
            if candle["low"] <= entry - self.sl_pips * self.pip_size:
                return {"exit_price": entry - self.sl_pips * self.pip_size, "reason": "STOP_LOSS"}
        else:
            if candle["high"] >= entry + self.sl_pips * self.pip_size:
                return {"exit_price": entry + self.sl_pips * self.pip_size, "reason": "STOP_LOSS"}

        if pos["candles_held"] >= self.max_candles:
            return {"exit_price": candle["close"], "reason": "TIME_EXIT"}

        return None

    def describe(self):
        return f"SL {self.sl_pips} pips, close after {self.max_candles} candles"


class IndicatorExit(ExitStrategy):
    """Fixed SL with indicator-based exit (e.g. RSI overbought)."""
    name = "Indicator Exit"

    def __init__(self, sl_pips=150, exit_indicator="M5_rsi_14",
                 exit_threshold=70, exit_direction="above", pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         exit_indicator=exit_indicator, exit_threshold=exit_threshold)
        self.sl_pips        = sl_pips
        self.exit_indicator  = exit_indicator
        self.exit_threshold  = exit_threshold
        self.exit_direction  = exit_direction

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        if direction == "BUY":
            if candle["low"] <= entry - self.sl_pips * self.pip_size:
                return {"exit_price": entry - self.sl_pips * self.pip_size, "reason": "STOP_LOSS"}
        else:
            if candle["high"] >= entry + self.sl_pips * self.pip_size:
                return {"exit_price": entry + self.sl_pips * self.pip_size, "reason": "STOP_LOSS"}

        if pos["candles_held"] >= 1:
            indicator_value = candle.get(self.exit_indicator)
            if indicator_value is not None:
                if self.exit_direction == "above" and indicator_value >= self.exit_threshold:
                    return {"exit_price": candle["close"],
                            "reason": f"INDICATOR_{self.exit_indicator}"}
                elif self.exit_direction == "below" and indicator_value <= self.exit_threshold:
                    return {"exit_price": candle["close"],
                            "reason": f"INDICATOR_{self.exit_indicator}"}

        return None

    def describe(self):
        return (f"SL {self.sl_pips} pips, exit when {self.exit_indicator} "
                f"{self.exit_direction} {self.exit_threshold}")


class HybridExit(ExitStrategy):
    """Combines trailing stop + time limit + breakeven move."""
    name = "Hybrid"

    def __init__(self, sl_pips=150, breakeven_activation_pips=50,
                 trail_distance_pips=100, max_candles=12, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         breakeven_activation_pips=breakeven_activation_pips,
                         trail_distance_pips=trail_distance_pips,
                         max_candles=max_candles)
        self.sl_pips      = sl_pips
        self.breakeven_pips = breakeven_activation_pips
        self.trail_pips    = trail_distance_pips
        self.max_candles   = max_candles

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        highest   = pos["highest_since_entry"]
        lowest    = pos["lowest_since_entry"]

        if direction == "BUY":
            fixed_sl    = entry - self.sl_pips * self.pip_size
            profit_pips = (highest - entry) / self.pip_size

            if profit_pips >= self.breakeven_pips:
                trail_sl     = highest - self.trail_pips * self.pip_size
                effective_sl = max(entry, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["low"] <= effective_sl:
                reason = "TRAILING" if effective_sl > fixed_sl else "STOP_LOSS"
                return {"exit_price": effective_sl, "reason": reason}
        else:
            fixed_sl    = entry + self.sl_pips * self.pip_size
            profit_pips = (entry - lowest) / self.pip_size

            if profit_pips >= self.breakeven_pips:
                trail_sl     = lowest + self.trail_pips * self.pip_size
                effective_sl = min(entry, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["high"] >= effective_sl:
                reason = "TRAILING" if effective_sl < fixed_sl else "STOP_LOSS"
                return {"exit_price": effective_sl, "reason": reason}

        if pos["candles_held"] >= self.max_candles:
            return {"exit_price": candle["close"], "reason": "TIME_EXIT"}

        return None

    def describe(self):
        return (f"SL {self.sl_pips}, BE at +{self.breakeven_pips}, "
                f"trail {self.trail_pips}, max {self.max_candles} candles")


# ── Factory ────────────────────────────────────────────────────────────────────

def get_default_exit_strategies(pip_size=0.01):
    """Return a list of exit strategies with default parameters for testing."""
    return [
        FixedSLTP(sl_pips=150, tp_pips=200,  pip_size=pip_size),
        FixedSLTP(sl_pips=150, tp_pips=300,  pip_size=pip_size),
        FixedSLTP(sl_pips=150, tp_pips=500,  pip_size=pip_size),
        TrailingStop(sl_pips=150, activation_pips=50,  trail_distance_pips=100, pip_size=pip_size),
        TrailingStop(sl_pips=150, activation_pips=100, trail_distance_pips=150, pip_size=pip_size),
        ATRBased(sl_atr_mult=1.5, tp_atr_mult=3.0),
        ATRBased(sl_atr_mult=2.0, tp_atr_mult=4.0),
        TimeBased(sl_pips=150, max_candles=6,  pip_size=pip_size),
        TimeBased(sl_pips=150, max_candles=12, pip_size=pip_size),
        IndicatorExit(sl_pips=150, exit_indicator="H1_rsi_14",
                      exit_threshold=70, exit_direction="above", pip_size=pip_size),
        HybridExit(sl_pips=150, breakeven_activation_pips=50,
                   trail_distance_pips=100, max_candles=12, pip_size=pip_size),
        HybridExit(sl_pips=150, breakeven_activation_pips=100,
                   trail_distance_pips=200, max_candles=24, pip_size=pip_size),
    ]

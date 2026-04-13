"""
EXIT STRATEGIES — Pluggable exit strategy implementations.
Each strategy decides when to close a position based on price action.
Used by the strategy backtester to test different exit approaches.
"""


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
        When both SL and TP could be hit in one candle, resolve the
        ambiguity conservatively by always picking SL.

        WHY: The old "closer to open = hit first" heuristic was
             geometrically wrong. Distance from open does NOT predict
             which level was hit first intra-bar. Without sub-bar
             (M1) data there's no way to know. Phase 8's candle_labeler
             fix applied the same reasoning — always pick SL on ties.
        CHANGED: April 2026 — conservative tie-break (audit HIGH,
                 matches candle_labeler fix)

        Returns: "SL" (also for ambiguous ties), "TP", or None.
        """
        candle_high = float(candle["high"])
        candle_low  = float(candle["low"])

        if direction == "BUY":
            sl_hit = candle_low  <= sl_price
            tp_hit = candle_high >= tp_price
            if sl_hit and tp_hit:
                return "SL"   # conservative: always pick SL on tie
            if sl_hit: return "SL"
            if tp_hit: return "TP"
        else:  # SELL
            sl_hit = candle_high >= sl_price
            tp_hit = candle_low  <= tp_price
            if sl_hit and tp_hit:
                return "SL"   # conservative: always pick SL on tie
            if sl_hit: return "SL"
            if tp_hit: return "TP"
        return None

    @staticmethod
    def _get_fill_price(candle, target_price, direction, is_sl=True):
        """
        Return actual fill price accounting for overnight/weekend gaps.
        If the candle opens past the target price the real fill is at
        candle open (which is always worse for SL, better for TP).
        """
        candle_open = float(candle["open"])
        if is_sl:
            if direction == "BUY"  and candle_open < target_price:
                return candle_open   # gapped down past SL
            if direction == "SELL" and candle_open > target_price:
                return candle_open   # gapped up past SL
        else:  # TP
            if direction == "BUY"  and candle_open > target_price:
                return candle_open   # gapped up past TP (lucky fill)
            if direction == "SELL" and candle_open < target_price:
                return candle_open   # gapped down past TP (lucky fill)
        return target_price


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
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
            return {"exit_price": fill, "reason": reason}
        if result == "TP":
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "TAKE_PROFIT_GAP" if fill != tp_price else "TAKE_PROFIT"
            return {"exit_price": fill, "reason": reason}
        return None

    def describe(self):
        return f"Fixed SL {self.sl_pips} pips / TP {self.tp_pips} pips"


class TrailingStop(ExitStrategy):
    """Fixed SL with trailing stop that activates after price moves in profit.

    WHY (Phase A.13): the original implementation had no take-profit and
         no max-hold limit. In a steady uptrend where every candle makes
         a new high, the trailing stop follows the price perfectly and
         never triggers — the trade runs to end-of-data (potentially
         millions of M5 candles), causing Run Backtest to hang at this
         combo. Real trailing-stop strategies always have a ceiling.
         Added optional tp_pips and max_candles parameters; both default
         to None for fully backward-compatible construction. The two
         entries in get_default_exit_strategies are updated below to
         pass sensible defaults so the hang stops out of the box.
    CHANGED: April 2026 — Phase A.13
    """
    name = "Trailing Stop"

    def __init__(self, sl_pips=150, activation_pips=50, trail_distance_pips=100,
                 tp_pips=None, max_candles=None, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         activation_pips=activation_pips,
                         trail_distance_pips=trail_distance_pips,
                         tp_pips=tp_pips, max_candles=max_candles)
        self.sl_pips             = sl_pips
        self.activation_pips     = activation_pips
        self.trail_distance_pips = trail_distance_pips
        # WHY (Phase A.13): tp_pips caps grinding profits; max_candles
        #      caps duration. Either alone is sufficient to prevent the
        #      hang. Both default to None to preserve old construction.
        # CHANGED: April 2026 — Phase A.13
        self.tp_pips     = tp_pips
        self.max_candles = max_candles

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        highest   = pos["highest_since_entry"]
        lowest    = pos["lowest_since_entry"]

        # WHY (Phase A.13): max_candles takes effect first — a
        #      time-based ceiling is the strongest guarantee against
        #      grinding-trend hangs.
        # CHANGED: April 2026 — Phase A.13
        if self.max_candles is not None:
            held = pos.get("candles_held", 0)
            if held >= self.max_candles:
                return {
                    "exit_price": float(candle["close"]),
                    "reason":     "TRAILING_MAX_CANDLES",
                }

        if direction == "BUY":
            fixed_sl    = entry - self.sl_pips * self.pip_size
            profit_pips = (highest - entry) / self.pip_size

            # WHY (Phase A.13): tp_pips check. If price has reached the
            #      take-profit ceiling intrabar (high crosses tp), exit
            #      at the tp price.
            # CHANGED: April 2026 — Phase A.13
            if self.tp_pips is not None:
                tp_price = entry + self.tp_pips * self.pip_size
                if candle["high"] >= tp_price:
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "TAKE_PROFIT"}

            if profit_pips >= self.activation_pips:
                trail_sl     = highest - self.trail_distance_pips * self.pip_size
                effective_sl = max(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["low"] <= effective_sl:
                fill = self._get_fill_price(candle, effective_sl, direction, is_sl=True)
                is_trailing = effective_sl > fixed_sl
                if fill != effective_sl:
                    reason = "TRAILING_STOP_GAP" if is_trailing else "STOP_LOSS_GAP"
                else:
                    reason = "TRAILING_STOP" if is_trailing else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}
        else:  # SELL
            fixed_sl    = entry + self.sl_pips * self.pip_size
            profit_pips = (entry - lowest) / self.pip_size

            # WHY (Phase A.13): tp_pips for SELL.
            # CHANGED: April 2026 — Phase A.13
            if self.tp_pips is not None:
                tp_price = entry - self.tp_pips * self.pip_size
                if candle["low"] <= tp_price:
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "TAKE_PROFIT"}

            if profit_pips >= self.activation_pips:
                trail_sl     = lowest + self.trail_distance_pips * self.pip_size
                effective_sl = min(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["high"] >= effective_sl:
                fill = self._get_fill_price(candle, effective_sl, direction, is_sl=True)
                is_trailing = effective_sl < fixed_sl
                if fill != effective_sl:
                    reason = "TRAILING_STOP_GAP" if is_trailing else "STOP_LOSS_GAP"
                else:
                    reason = "TRAILING_STOP" if is_trailing else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}

        return None

    def describe(self):
        parts = [
            f"SL {self.sl_pips} pips",
            f"trail after +{self.activation_pips} pips",
            f"trail distance {self.trail_distance_pips} pips",
        ]
        if self.tp_pips is not None:
            parts.append(f"TP {self.tp_pips} pips")
        if self.max_candles is not None:
            parts.append(f"max {self.max_candles} candles")
        return ", ".join(parts)


class ATRBased(ExitStrategy):
    """SL and TP based on ATR (adapts to volatility)."""
    name = "ATR-Based"

    # WHY (Phase 31 Fix 8): Old code had a silent 5.0 fallback when the
    #      ATR column was missing or NaN. 5.0 is in raw price units —
    #      for XAUUSD pip_size=0.01 that's 500 pips of SL; for EURUSD
    #      pip_size=0.0001 that's 50,000 pips. Neither is defensible.
    #      Replace the silent fallback with a None sentinel + WARNING
    #      log (once per strategy instance). on_new_candle returns None
    #      when _entry_atr is None, so the trade naturally runs to the
    #      next exit condition instead of firing a fake SL/TP.
    # WHY (Phase 31 Fix 8 cont.): atr_column default 'H1_atr_14' fails
    #      silently on non-H1 backtests. Keep the default for XAUUSD H1
    #      backward-compat but the warning now surfaces the problem.
    # CHANGED: April 2026 — Phase 31 Fix 8 — no silent ATR fallback
    #          (audit Part C HIGH #13 + #14)
    def __init__(self, sl_atr_mult=1.5, tp_atr_mult=3.0, atr_column="H1_atr_14",
                 max_candles=1000):
        super().__init__(sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult,
                         max_candles=max_candles)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.atr_column  = atr_column
        # WHY (Phase A.14): defensive max-hold cap. Without it, trades
        #      where the ATR column is missing at entry (_entry_atr=None)
        #      run to end-of-data and hang Run Backtest. Also catches
        #      degenerate trades that drift indefinitely without hitting
        #      either SL or TP.
        # CHANGED: April 2026 — Phase A.14
        self.max_candles = max_candles
        self._entry_atr  = None
        self._missing_atr_warned = False

    def on_entry(self, candle):
        """Called when position is opened — capture ATR at entry.

        Sets self._entry_atr to None if the ATR column is missing or NaN.
        on_new_candle will then refuse to fire SL/TP exits, letting the
        trade run to the next exit condition (time-based, etc.).
        """
        raw = candle.get(self.atr_column, None)
        # pandas NaN is not None — test explicitly
        if raw is None:
            self._entry_atr = None
        else:
            try:
                atr_val = float(raw)
                # NaN check: NaN != NaN
                if atr_val != atr_val or atr_val <= 0:
                    self._entry_atr = None
                else:
                    self._entry_atr = atr_val
            except (TypeError, ValueError):
                self._entry_atr = None

        if self._entry_atr is None and not self._missing_atr_warned:
            try:
                from shared.logging_setup import get_logger
                _log = get_logger(__name__)
                _log.warning(
                    f"[ATRBased] ATR column '{self.atr_column}' missing or invalid "
                    f"at entry candle. SL/TP exits will NOT fire — trade runs to "
                    f"other exit conditions. (Warning shown once per strategy instance.)"
                )
            except Exception:
                pass
            self._missing_atr_warned = True

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        # WHY (Phase A.14): max-hold cap fires before any other logic so
        #      both the missing-ATR path and the slow-drift path are
        #      bounded. ATR_NO_DATA reason makes the missing-ATR cause
        #      visible in stats vs ATR_TIME_EXIT for normal grind.
        # CHANGED: April 2026 — Phase A.14
        if pos.get("candles_held", 0) >= self.max_candles:
            reason = "ATR_NO_DATA" if self._entry_atr is None else "ATR_TIME_EXIT"
            return {"exit_price": float(candle["close"]), "reason": reason}

        # WHY: Old code had `atr = self._entry_atr or 5.0` — silent
        #      fallback. Now when ATR is None, return None so the trade
        #      runs to the next exit condition without firing fake SL/TP.
        # CHANGED: April 2026 — Phase 31 Fix 8 — None-guard
        if self._entry_atr is None:
            return None
        atr = self._entry_atr

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
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "ATR_STOP_LOSS_GAP" if fill != sl_price else "ATR_STOP_LOSS"
            return {"exit_price": fill, "reason": reason}
        if result == "TP":
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "ATR_TAKE_PROFIT_GAP" if fill != tp_price else "ATR_TAKE_PROFIT"
            return {"exit_price": fill, "reason": reason}
        return None

    def describe(self):
        return (f"SL {self.sl_atr_mult}xATR, TP {self.tp_atr_mult}xATR, "
                f"max {self.max_candles} candles")


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
            sl_price = entry - self.sl_pips * self.pip_size
            if candle["low"] <= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}
        else:
            sl_price = entry + self.sl_pips * self.pip_size
            if candle["high"] >= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}

        if pos["candles_held"] >= self.max_candles:
            return {"exit_price": candle["close"], "reason": "TIME_EXIT"}

        return None

    def describe(self):
        return f"SL {self.sl_pips} pips, close after {self.max_candles} candles"


class IndicatorExit(ExitStrategy):
    """Fixed SL with indicator-based exit (e.g. RSI overbought)."""
    name = "Indicator Exit"

    def __init__(self, sl_pips=150, exit_indicator="M5_rsi_14",
                 exit_threshold=70, exit_direction="above",
                 max_candles=500, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         exit_indicator=exit_indicator, exit_threshold=exit_threshold,
                         max_candles=max_candles)
        self.sl_pips        = sl_pips
        self.exit_indicator  = exit_indicator
        self.exit_threshold  = exit_threshold
        self.exit_direction  = exit_direction
        # WHY (Phase A.14): defensive max-hold cap. Without it, trades
        #      that drift in profit while the exit indicator never
        #      crosses its threshold run to end-of-data.
        # CHANGED: April 2026 — Phase A.14
        self.max_candles    = max_candles

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        if direction == "BUY":
            sl_price = entry - self.sl_pips * self.pip_size
            if candle["low"] <= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}
        else:
            sl_price = entry + self.sl_pips * self.pip_size
            if candle["high"] >= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}

        if pos["candles_held"] >= 1:
            indicator_value = candle.get(self.exit_indicator)
            if indicator_value is not None:
                if self.exit_direction == "above" and indicator_value >= self.exit_threshold:
                    return {"exit_price": candle["close"],
                            "reason": f"INDICATOR_{self.exit_indicator}"}
                elif self.exit_direction == "below" and indicator_value <= self.exit_threshold:
                    return {"exit_price": candle["close"],
                            "reason": f"INDICATOR_{self.exit_indicator}"}

        # WHY (Phase A.14): max-hold cap. If price drifts in profit and
        #      the indicator never crosses its threshold, the trade
        #      otherwise ran to end-of-data and hung Run Backtest.
        # CHANGED: April 2026 — Phase A.14
        if pos.get("candles_held", 0) >= self.max_candles:
            return {"exit_price": float(candle["close"]),
                    "reason": "INDICATOR_TIME_EXIT"}

        return None

    def describe(self):
        return (f"SL {self.sl_pips} pips, exit when {self.exit_indicator} "
                f"{self.exit_direction} {self.exit_threshold}, "
                f"max {self.max_candles} candles")


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
                fill = self._get_fill_price(candle, effective_sl, direction, is_sl=True)
                is_trailing = effective_sl > fixed_sl
                if fill != effective_sl:
                    reason = "TRAILING_GAP" if is_trailing else "STOP_LOSS_GAP"
                else:
                    reason = "TRAILING" if is_trailing else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}
        else:
            fixed_sl    = entry + self.sl_pips * self.pip_size
            profit_pips = (entry - lowest) / self.pip_size

            if profit_pips >= self.breakeven_pips:
                trail_sl     = lowest + self.trail_pips * self.pip_size
                effective_sl = min(entry, trail_sl)
            else:
                effective_sl = fixed_sl

            if candle["high"] >= effective_sl:
                fill = self._get_fill_price(candle, effective_sl, direction, is_sl=True)
                is_trailing = effective_sl < fixed_sl
                if fill != effective_sl:
                    reason = "TRAILING_GAP" if is_trailing else "STOP_LOSS_GAP"
                else:
                    reason = "TRAILING" if is_trailing else "STOP_LOSS"
                return {"exit_price": fill, "reason": reason}

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
        TrailingStop(sl_pips=150, activation_pips=50,  trail_distance_pips=100,
                     tp_pips=750, max_candles=1000, pip_size=pip_size),
        TrailingStop(sl_pips=150, activation_pips=100, trail_distance_pips=150,
                     tp_pips=750, max_candles=1000, pip_size=pip_size),
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

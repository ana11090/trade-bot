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
    """Fixed stop loss and take profit in pips.

    WHY (Phase A.28.2): Old version had no max-hold ceiling. A trade
         that opened during a long sideways period could drift for
         the entire test window without triggering either SL or TP,
         hit END_OF_DATA, and (combined with the END_OF_DATA lockout
         in fast_backtest) lock out every subsequent signal. Result:
         many rule × exit combos produced 1-5 trades when the data
         actually contained thousands of viable signals.
         Added optional max_candles parameter; default None preserves
         old behavior for any external caller, but get_default_exit_strategies
         now passes max_candles=1000 so the matrix runs out of the
         box without trade-count collapse.
    CHANGED: April 2026 — Phase A.28.2
    """
    name = "Fixed SL/TP"

    def __init__(self, sl_pips=150, tp_pips=300, max_candles=None, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         tp_pips=tp_pips, max_candles=max_candles)
        self.sl_pips     = sl_pips
        self.tp_pips     = tp_pips
        self.max_candles = max_candles

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        # WHY (Phase A.28.2): Time-based ceiling — checked first because
        #      the iterative path calls this once per candle and we want
        #      to cut hold time before any other check. The vectorized
        #      path enforces max_candles separately at the numpy layer
        #      so this branch is only used by non-vectorized callers.
        # CHANGED: April 2026 — Phase A.28.2
        if self.max_candles is not None:
            held = pos.get("candles_held", 0)
            if held >= self.max_candles:
                return {
                    "exit_price": float(candle["close"]),
                    "reason":     "FIXED_MAX_CANDLES",
                }

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
        if self.max_candles is not None:
            return f"Fixed SL {self.sl_pips} pips / TP {self.tp_pips} pips / max {self.max_candles} candles"
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
    name = "ATR Only"

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


class ATRFixedSLTP(ExitStrategy):
    """ATR-proportional SL/TP — adapts to volatility, then holds fixed.

    WHY: Default FixedSLTP uses sl_pips=150, but H1 ATR on XAUUSD in 2026
         averages 4,371 pips. A 150-pip SL = 3.4% of ATR — pure noise.
         This class reads ATR at entry time and sets SL/TP as multiples
         of it. Once set, the levels are fixed (no trailing), so the
         exit logic is identical to FixedSLTP.

         Unlike ATRBased (which works in raw price units internally),
         this class converts to pips immediately and stores self.sl_pips
         and self.tp_pips so that:
         - _expected_sl_pips_for_exit() Path 1 reads self.sl_pips directly
         - Lot sizing is correct per trade
         - The on_new_candle logic is simple fixed SL/TP

    CHANGED: April 2026 — ATR-adaptive exits for high-volatility instruments
    """
    name = "ATR Fixed SL/TP"

    def __init__(self, sl_atr_mult=1.0, tp_atr_mult=2.5, atr_column="H1_atr_14",
                 max_candles=200, pip_size=0.01):
        super().__init__(pip_size=pip_size, sl_atr_mult=sl_atr_mult,
                         tp_atr_mult=tp_atr_mult, max_candles=max_candles)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.atr_column  = atr_column
        self.max_candles  = max_candles
        # WHY: sl_pips and tp_pips are set per trade in on_entry().
        #      Default to 150/300 so the strategy is safe to use even
        #      if on_entry is never called (backward compat with any
        #      code path that skips the hook).
        # CHANGED: April 2026 — safe defaults
        self.sl_pips = 150
        self.tp_pips = 300
        self._entry_atr = None
        self._missing_atr_warned = False

    def on_entry(self, candle):
        """Called when position opens — compute SL/TP from ATR at entry.

        Sets self.sl_pips and self.tp_pips in pips (not price units).
        If ATR is missing or NaN, keeps the previous values (or defaults).
        """
        raw = candle.get(self.atr_column, None)
        if raw is None:
            self._entry_atr = None
        else:
            try:
                atr_val = float(raw)
                if atr_val != atr_val or atr_val <= 0:  # NaN check
                    self._entry_atr = None
                else:
                    self._entry_atr = atr_val
            except (TypeError, ValueError):
                self._entry_atr = None

        if self._entry_atr is not None and self.pip_size > 0:
            # WHY: Convert price-unit ATR to pips, then apply multipliers.
            #      round() avoids floating-point dust in SL/TP comparisons.
            # CHANGED: April 2026 — ATR to pips conversion
            self.sl_pips = max(10, round(self._entry_atr * self.sl_atr_mult / self.pip_size))
            self.tp_pips = max(20, round(self._entry_atr * self.tp_atr_mult / self.pip_size))
        else:
            if not self._missing_atr_warned:
                try:
                    from shared.logging_setup import get_logger
                    _log = get_logger(__name__)
                    _log.warning(
                        f"[ATRFixedSLTP] ATR column '{self.atr_column}' missing or "
                        f"invalid at entry. Using fallback sl_pips={self.sl_pips}, "
                        f"tp_pips={self.tp_pips}. (Warning shown once.)"
                    )
                except Exception:
                    pass
                self._missing_atr_warned = True

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        # WHY: Time-based ceiling — same as FixedSLTP.
        # CHANGED: April 2026 — max hold cap
        if self.max_candles is not None:
            held = pos.get("candles_held", 0)
            if held >= self.max_candles:
                reason = "ATR_FIXED_MAX_CANDLES"
                if self._entry_atr is None:
                    reason = "ATR_FIXED_NO_DATA"
                return {
                    "exit_price": float(candle["close"]),
                    "reason":     reason,
                }

        if direction == "BUY":
            sl_price = entry - self.sl_pips * self.pip_size
            tp_price = entry + self.tp_pips * self.pip_size
        else:
            sl_price = entry + self.sl_pips * self.pip_size
            tp_price = entry - self.tp_pips * self.pip_size

        result = self._resolve_sl_tp_priority(candle, sl_price, tp_price, direction)
        if result == "SL":
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "ATR_FIXED_SL_GAP" if fill != sl_price else "ATR_FIXED_SL"
            return {"exit_price": fill, "reason": reason}
        if result == "TP":
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "ATR_FIXED_TP_GAP" if fill != tp_price else "ATR_FIXED_TP"
            return {"exit_price": fill, "reason": reason}
        return None

    def describe(self):
        return (f"ATR Fixed SL {self.sl_atr_mult}x / TP {self.tp_atr_mult}x, "
                f"max {self.max_candles} candles")


class ATRBreakevenTrail(ExitStrategy):
    """ATR-based SL → breakeven lock → trailing stop → hard TP.

    WHY: Prop firm evals have tight trailing DD limits. Once a trade
         moves 1× ATR in profit, locking SL at breakeven means the
         trade cannot add to drawdown. The trailing phase then captures
         trend profits while the hard TP caps hold time.
         All distances scale with ATR so the strategy works across
         any instrument or volatility regime.
    CHANGED: April 2026 — DD-safe ATR trailing exit
    """
    name = "ATR BE Trail"

    def __init__(self, sl_atr_mult=1.0, breakeven_atr_mult=1.0,
                 trail_activation_atr_mult=1.5, trail_atr_mult=1.0,
                 tp_atr_mult=3.0, atr_column="H1_atr_14",
                 max_candles=200, pip_size=0.01):
        super().__init__(pip_size=pip_size,
                         sl_atr_mult=sl_atr_mult,
                         breakeven_atr_mult=breakeven_atr_mult,
                         trail_activation_atr_mult=trail_activation_atr_mult,
                         trail_atr_mult=trail_atr_mult,
                         tp_atr_mult=tp_atr_mult,
                         max_candles=max_candles)
        self.sl_atr_mult               = sl_atr_mult
        self.breakeven_atr_mult        = breakeven_atr_mult
        self.trail_activation_atr_mult = trail_activation_atr_mult
        self.trail_atr_mult            = trail_atr_mult
        self.tp_atr_mult               = tp_atr_mult
        self.atr_column                = atr_column
        self.max_candles               = max_candles
        # WHY: sl_pips set in on_entry() for lot sizing via
        #      _expected_sl_pips_for_exit() Path 1. Defaults to 150
        #      for safety if on_entry is never called.
        # CHANGED: April 2026 — safe default for lot sizing
        self.sl_pips     = 150
        self._entry_atr  = None
        self._sl_price   = None
        self._tp_price   = None
        self._be_distance_price  = None
        self._trail_activation_price_dist = None
        self._trail_distance_price = None
        self._breakeven_locked = False
        self._missing_atr_warned = False

    def on_entry(self, candle):
        """Read ATR at entry, pre-compute all distance thresholds."""
        raw = candle.get(self.atr_column, None)
        self._breakeven_locked = False

        if raw is None:
            self._entry_atr = None
        else:
            try:
                atr_val = float(raw)
                if atr_val != atr_val or atr_val <= 0:
                    self._entry_atr = None
                else:
                    self._entry_atr = atr_val
            except (TypeError, ValueError):
                self._entry_atr = None

        if self._entry_atr is not None and self.pip_size > 0:
            atr = self._entry_atr
            # WHY: Pre-compute all price distances once at entry.
            #      on_new_candle just compares against these — no
            #      per-candle ATR lookups needed.
            # CHANGED: April 2026 — pre-compute at entry
            self._sl_price_dist     = atr * self.sl_atr_mult
            self._be_distance_price = atr * self.breakeven_atr_mult
            self._trail_activation_price_dist = atr * self.trail_activation_atr_mult
            self._trail_distance_price = atr * self.trail_atr_mult
            self._tp_price_dist     = atr * self.tp_atr_mult
            # WHY: Set sl_pips for lot sizing via _expected_sl_pips_for_exit()
            # CHANGED: April 2026 — lot sizing awareness
            self.sl_pips = max(10, round(self._sl_price_dist / self.pip_size))
        else:
            # Fallback: use fixed distances if ATR missing
            self._sl_price_dist     = 150 * self.pip_size
            self._be_distance_price = 150 * self.pip_size
            self._trail_activation_price_dist = 225 * self.pip_size
            self._trail_distance_price = 150 * self.pip_size
            self._tp_price_dist     = 450 * self.pip_size
            self.sl_pips = 150

            if not self._missing_atr_warned:
                try:
                    from shared.logging_setup import get_logger
                    _log = get_logger(__name__)
                    _log.warning(
                        f"[ATRBreakevenTrail] ATR column '{self.atr_column}' "
                        f"missing or invalid at entry. Using fixed fallback "
                        f"sl=150 pips. (Warning shown once.)"
                    )
                except Exception:
                    pass
                self._missing_atr_warned = True

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        highest   = pos["highest_since_entry"]
        lowest    = pos["lowest_since_entry"]

        # WHY: Max hold cap — strongest guarantee against hanging.
        # CHANGED: April 2026 — max hold safety
        if self.max_candles is not None:
            if pos.get("candles_held", 0) >= self.max_candles:
                reason = "ATRBE_MAX_CANDLES"
                if self._entry_atr is None:
                    reason = "ATRBE_NO_DATA"
                return {"exit_price": float(candle["close"]), "reason": reason}

        if direction == "BUY":
            # Hard TP check first
            tp_price = entry + self._tp_price_dist
            if candle["high"] >= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                return {"exit_price": fill, "reason": reason}

            # Compute current profit from best price
            profit_from_entry = highest - entry

            # Phase 3: Trailing (activates after trail_activation distance)
            if profit_from_entry >= self._trail_activation_price_dist:
                trail_sl = highest - self._trail_distance_price
                # WHY: Trail SL must be at least at breakeven. Never
                #      trail BELOW entry once breakeven was reached.
                # CHANGED: April 2026 — floor trail at entry
                trail_sl = max(trail_sl, entry)
                if candle["low"] <= trail_sl:
                    fill = self._get_fill_price(candle, trail_sl, direction, is_sl=True)
                    reason = "ATRBE_TRAIL_GAP" if fill != trail_sl else "ATRBE_TRAIL"
                    return {"exit_price": fill, "reason": reason}

            # Phase 2: Breakeven lock
            elif profit_from_entry >= self._be_distance_price:
                self._breakeven_locked = True
                if candle["low"] <= entry:
                    fill = self._get_fill_price(candle, entry, direction, is_sl=True)
                    reason = "ATRBE_BREAKEVEN_GAP" if fill != entry else "ATRBE_BREAKEVEN"
                    return {"exit_price": fill, "reason": reason}

            # Phase 1: Initial ATR SL
            else:
                sl_price = entry - self._sl_price_dist
                if candle["low"] <= sl_price:
                    fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                    reason = "ATRBE_SL_GAP" if fill != sl_price else "ATRBE_SL"
                    return {"exit_price": fill, "reason": reason}

        else:  # SELL
            # Hard TP
            tp_price = entry - self._tp_price_dist
            if candle["low"] <= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                return {"exit_price": fill, "reason": reason}

            profit_from_entry = entry - lowest

            # Phase 3: Trailing
            if profit_from_entry >= self._trail_activation_price_dist:
                trail_sl = lowest + self._trail_distance_price
                trail_sl = min(trail_sl, entry)  # floor at breakeven
                if candle["high"] >= trail_sl:
                    fill = self._get_fill_price(candle, trail_sl, direction, is_sl=True)
                    reason = "ATRBE_TRAIL_GAP" if fill != trail_sl else "ATRBE_TRAIL"
                    return {"exit_price": fill, "reason": reason}

            # Phase 2: Breakeven
            elif profit_from_entry >= self._be_distance_price:
                self._breakeven_locked = True
                if candle["high"] >= entry:
                    fill = self._get_fill_price(candle, entry, direction, is_sl=True)
                    reason = "ATRBE_BREAKEVEN_GAP" if fill != entry else "ATRBE_BREAKEVEN"
                    return {"exit_price": fill, "reason": reason}

            # Phase 1: Initial SL
            else:
                sl_price = entry + self._sl_price_dist
                if candle["high"] >= sl_price:
                    fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                    reason = "ATRBE_SL_GAP" if fill != sl_price else "ATRBE_SL"
                    return {"exit_price": fill, "reason": reason}

        return None

    def describe(self):
        return (f"ATR BE Trail: SL {self.sl_atr_mult}x, "
                f"BE at {self.breakeven_atr_mult}x, "
                f"trail at {self.trail_activation_atr_mult}x / "
                f"{self.trail_atr_mult}x, TP {self.tp_atr_mult}x, "
                f"max {self.max_candles} candles")


class PSARExit(ExitStrategy):
    """ATR-based SL + Parabolic SAR trend reversal exit.

    WHY: PSAR is purpose-built for trailing trends. It stays in winning
         trades longer than fixed TP during strong trends, and exits
         faster than wide ATR TP during quick reversals.
         ATR SL underneath protects against gap moves where PSAR hasn't
         flipped yet. Hard TP caps grind-forever scenarios.

         Uses psar_signal column (1.0 = bullish, 0.0 = bearish) rather
         than comparing psar price to candle price — avoids floating-point
         edge cases where psar ≈ close.
    CHANGED: April 2026 — PSAR-based exit with ATR safety net
    """
    name = "PSAR Exit"

    def __init__(self, sl_atr_mult=1.5, tp_atr_mult=4.0,
                 atr_column="H1_atr_14",
                 psar_signal_column="H1_psar_signal",
                 min_candles_before_psar=2,
                 max_candles=200, pip_size=0.01):
        super().__init__(pip_size=pip_size,
                         sl_atr_mult=sl_atr_mult,
                         tp_atr_mult=tp_atr_mult,
                         max_candles=max_candles)
        self.sl_atr_mult          = sl_atr_mult
        self.tp_atr_mult          = tp_atr_mult
        self.atr_column           = atr_column
        self.psar_signal_column   = psar_signal_column
        # WHY: Skip PSAR check for the first N candles after entry.
        #      PSAR sometimes hasn't "caught up" to a new entry yet —
        #      it can still be flipped against the trade from the prior
        #      move. Giving it 2 candles to settle avoids false exits
        #      on the very first bar.
        # CHANGED: April 2026 — min candles before PSAR check
        self.min_candles_before_psar = min_candles_before_psar
        self.max_candles           = max_candles
        # WHY: sl_pips for lot sizing via _expected_sl_pips_for_exit()
        # CHANGED: April 2026 — lot sizing awareness
        self.sl_pips = 150  # default, updated in on_entry
        self._entry_atr = None
        self._sl_price_dist = None
        self._tp_price_dist = None
        self._missing_atr_warned = False

    def on_entry(self, candle):
        """Read ATR at entry, compute SL/TP distances."""
        raw = candle.get(self.atr_column, None)
        if raw is None:
            self._entry_atr = None
        else:
            try:
                atr_val = float(raw)
                if atr_val != atr_val or atr_val <= 0:
                    self._entry_atr = None
                else:
                    self._entry_atr = atr_val
            except (TypeError, ValueError):
                self._entry_atr = None

        if self._entry_atr is not None and self.pip_size > 0:
            atr = self._entry_atr
            self._sl_price_dist = atr * self.sl_atr_mult
            self._tp_price_dist = atr * self.tp_atr_mult
            self.sl_pips = max(10, round(self._sl_price_dist / self.pip_size))
        else:
            self._sl_price_dist = 150 * self.pip_size
            self._tp_price_dist = 450 * self.pip_size
            self.sl_pips = 150
            if not self._missing_atr_warned:
                try:
                    from shared.logging_setup import get_logger
                    _log = get_logger(__name__)
                    _log.warning(
                        f"[PSARExit] ATR column '{self.atr_column}' missing or "
                        f"invalid at entry. Using fixed fallback sl=150 pips. "
                        f"(Warning shown once.)"
                    )
                except Exception:
                    pass
                self._missing_atr_warned = True

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]

        # Max hold cap
        if self.max_candles is not None:
            if pos.get("candles_held", 0) >= self.max_candles:
                reason = "PSAR_MAX_CANDLES"
                if self._entry_atr is None:
                    reason = "PSAR_NO_DATA"
                return {"exit_price": float(candle["close"]), "reason": reason}

        if direction == "BUY":
            # Hard TP
            tp_price = entry + self._tp_price_dist
            if candle["high"] >= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "PSAR_TP_GAP" if fill != tp_price else "PSAR_TP"
                return {"exit_price": fill, "reason": reason}

            # ATR SL (safety net — always active)
            sl_price = entry - self._sl_price_dist
            if candle["low"] <= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "PSAR_SL_GAP" if fill != sl_price else "PSAR_SL"
                return {"exit_price": fill, "reason": reason}

            # PSAR flip check (after min candles settle period)
            if pos.get("candles_held", 0) >= self.min_candles_before_psar:
                psar_signal = candle.get(self.psar_signal_column)
                if psar_signal is not None:
                    try:
                        # WHY: BUY exit when PSAR flips bearish (signal = 0.0).
                        #      psar_signal: 1.0 = bullish, 0.0 = bearish.
                        # CHANGED: April 2026 — PSAR flip detection
                        if float(psar_signal) == 0.0:
                            return {
                                "exit_price": float(candle["close"]),
                                "reason": "PSAR_FLIP"
                            }
                    except (TypeError, ValueError):
                        pass

        else:  # SELL
            # Hard TP
            tp_price = entry - self._tp_price_dist
            if candle["low"] <= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "PSAR_TP_GAP" if fill != tp_price else "PSAR_TP"
                return {"exit_price": fill, "reason": reason}

            # ATR SL
            sl_price = entry + self._sl_price_dist
            if candle["high"] >= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "PSAR_SL_GAP" if fill != sl_price else "PSAR_SL"
                return {"exit_price": fill, "reason": reason}

            # PSAR flip check
            if pos.get("candles_held", 0) >= self.min_candles_before_psar:
                psar_signal = candle.get(self.psar_signal_column)
                if psar_signal is not None:
                    try:
                        # WHY: SELL exit when PSAR flips bullish (signal = 1.0).
                        # CHANGED: April 2026 — PSAR flip detection
                        if float(psar_signal) == 1.0:
                            return {
                                "exit_price": float(candle["close"]),
                                "reason": "PSAR_FLIP"
                            }
                    except (TypeError, ValueError):
                        pass

        return None

    def describe(self):
        return (f"PSAR exit ({self.psar_signal_column}), "
                f"ATR SL {self.sl_atr_mult}x, TP {self.tp_atr_mult}x, "
                f"settle {self.min_candles_before_psar} candles, "
                f"max {self.max_candles} candles")


class ATRTrailing(ExitStrategy):
    """ATR-based SL/TP with trailing stop — matches what EA generator produces.

    WHY: The EA generator always adds trailing stop to ATR exits. The
         backtester's ATRBased class has NO trailing. This causes backtester
         results to differ from live EA results. ATRTrailing matches the EA.
    CHANGED: April 2026 — sync backtester with EA behavior
    """
    name = "ATR + Trailing"

    def __init__(self, sl_atr_mult=2.0, tp_atr_mult=4.0, atr_column="H1_atr_14",
                 activation_pips=50, trail_distance_pips=100,
                 max_candles=1000, pip_size=0.01):
        super().__init__(sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult,
                         activation_pips=activation_pips,
                         trail_distance_pips=trail_distance_pips,
                         max_candles=max_candles, pip_size=pip_size)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.atr_column = atr_column
        self.activation_pips = activation_pips
        self.trail_distance_pips = trail_distance_pips
        self.max_candles = max_candles
        self.pip_size = pip_size
        self._entry_atr = None

    def on_entry(self, candle):
        raw = candle.get(self.atr_column, None)
        if raw is None:
            self._entry_atr = None
        else:
            try:
                atr_val = float(raw)
                if atr_val != atr_val or atr_val <= 0:
                    self._entry_atr = None
                else:
                    self._entry_atr = atr_val
            except (TypeError, ValueError):
                self._entry_atr = None

    def on_new_candle(self, candle, pos):
        entry = pos["entry_price"]
        direction = pos["direction"]
        highest = pos["highest_since_entry"]
        lowest = pos["lowest_since_entry"]

        if pos.get("candles_held", 0) >= self.max_candles:
            return {"exit_price": float(candle["close"]),
                    "reason": "ATR_TRAIL_MAX_CANDLES"}

        if self._entry_atr is None:
            return None

        atr = self._entry_atr
        sl_distance = atr * self.sl_atr_mult
        tp_distance = atr * self.tp_atr_mult

        if direction == "BUY":
            sl_price = entry - sl_distance
            tp_price = entry + tp_distance

            profit_pips = (highest - entry) / self.pip_size
            if profit_pips >= self.activation_pips:
                trail_sl = highest - self.trail_distance_pips * self.pip_size
                if trail_sl > sl_price:
                    sl_price = trail_sl

            if candle["high"] >= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}
            if candle["low"] <= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                return {"exit_price": fill, "reason": "ATR_TRAIL_SL"}
        else:
            sl_price = entry + sl_distance
            tp_price = entry - tp_distance

            profit_pips = (entry - lowest) / self.pip_size
            if profit_pips >= self.activation_pips:
                trail_sl = lowest + self.trail_distance_pips * self.pip_size
                if trail_sl < sl_price:
                    sl_price = trail_sl

            if candle["low"] <= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}
            if candle["high"] >= sl_price:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                return {"exit_price": fill, "reason": "ATR_TRAIL_SL"}

        return None

    @property
    def sl_pips(self):
        if self._entry_atr:
            return (self._entry_atr * self.sl_atr_mult) / self.pip_size
        return 150

    def describe(self):
        return (f"SL {self.sl_atr_mult}xATR, TP {self.tp_atr_mult}xATR, "
                f"trail after +{self.activation_pips} pips ({self.trail_distance_pips} dist), "
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

# WHY (T1c): ATR exits default atr_column='H1_atr_14'. Backtesting M5 or
#      M15 entries then uses H1 volatility for SL/TP sizing — 10×+ too
#      wide on XAUUSD. Propagating the entry_tf here aligns the ATR
#      column to the entry TF so sizing matches the signal frequency.
#      entry_tf=None preserves old H1 default for any caller that
#      doesn't know the entry TF (backward compat).
# CHANGED: April 2026 — T1c — entry_tf-aware ATR exit defaults
def get_default_exit_strategies(pip_size=0.01, entry_tf=None):
    """Return a list of exit strategies with default parameters for testing."""
    # WHY (Phase A.28.2): Pass max_candles=1000 to every FixedSLTP so a
    #      trade can not drift for the entire test window. On M5 that
    #      is ~3.5 days of hold time — generous for any fixed-SL/TP
    #      strategy. Without this ceiling, FixedSLTP combos where the
    #      first trade does not hit SL/TP would lock out every
    #      subsequent signal in the backtest via the END_OF_DATA
    #      lockout in fast_backtest.
    # CHANGED: April 2026 — Phase A.28.2
    _atr_col = f"{entry_tf}_atr_14" if entry_tf else "H1_atr_14"
    return [
        FixedSLTP(sl_pips=150, tp_pips=200,  max_candles=1000, pip_size=pip_size),
        FixedSLTP(sl_pips=150, tp_pips=300,  max_candles=1000, pip_size=pip_size),
        FixedSLTP(sl_pips=150, tp_pips=500,  max_candles=1000, pip_size=pip_size),
        # WHY: ATR-proportional fixed exits. SL/TP scale with current
        #      volatility — 150/300 was fine when H1 ATR was 400-600
        #      pips (2021-2023), but ATR in 2025-2026 is 1500-4400.
        #      These adapt automatically.
        # CHANGED: April 2026 — ATR-scaled exits
        ATRFixedSLTP(sl_atr_mult=1.0, tp_atr_mult=2.0, max_candles=100,
                     pip_size=pip_size, atr_column=_atr_col),
        ATRFixedSLTP(sl_atr_mult=1.0, tp_atr_mult=3.0, max_candles=200,
                     pip_size=pip_size, atr_column=_atr_col),
        ATRFixedSLTP(sl_atr_mult=1.5, tp_atr_mult=3.0, max_candles=200,
                     pip_size=pip_size, atr_column=_atr_col),
        # WHY: Breakeven trail — once in profit, trade can't add to DD.
        #      Critical for prop firm evals with tight trailing DD.
        # CHANGED: April 2026 — DD-safe ATR trailing
        ATRBreakevenTrail(sl_atr_mult=1.0, breakeven_atr_mult=1.0,
                          trail_activation_atr_mult=1.5, trail_atr_mult=1.0,
                          tp_atr_mult=3.0, max_candles=200,
                          pip_size=pip_size, atr_column=_atr_col),
        ATRBreakevenTrail(sl_atr_mult=1.0, breakeven_atr_mult=0.7,
                          trail_activation_atr_mult=1.0, trail_atr_mult=0.8,
                          tp_atr_mult=4.0, max_candles=300,
                          pip_size=pip_size, atr_column=_atr_col),
        # WHY: PSAR trend-following exit. Stays in trends longer than
        #      fixed TP, exits faster on reversals. ATR SL as safety net.
        # CHANGED: April 2026 — PSAR exit
        PSARExit(sl_atr_mult=1.5, tp_atr_mult=4.0,
                 psar_signal_column=f"{entry_tf}_psar_signal" if entry_tf else "H1_psar_signal",
                 atr_column=_atr_col, max_candles=200, pip_size=pip_size),
        PSARExit(sl_atr_mult=1.0, tp_atr_mult=3.0,
                 psar_signal_column=f"{entry_tf}_psar_signal" if entry_tf else "H1_psar_signal",
                 atr_column=_atr_col, min_candles_before_psar=3,
                 max_candles=150, pip_size=pip_size),
        TrailingStop(sl_pips=150, activation_pips=50,  trail_distance_pips=100,
                     tp_pips=750, max_candles=1000, pip_size=pip_size),
        TrailingStop(sl_pips=150, activation_pips=100, trail_distance_pips=150,
                     tp_pips=750, max_candles=1000, pip_size=pip_size),
        ATRBased(sl_atr_mult=1.5, tp_atr_mult=3.0, atr_column=_atr_col),
        ATRBased(sl_atr_mult=2.0, tp_atr_mult=4.0, atr_column=_atr_col),
        ATRTrailing(sl_atr_mult=2.0, tp_atr_mult=4.0, activation_pips=50,
                    trail_distance_pips=100, pip_size=pip_size, atr_column=_atr_col),
        TimeBased(sl_pips=150, max_candles=6,  pip_size=pip_size),
        TimeBased(sl_pips=150, max_candles=12, pip_size=pip_size),
        IndicatorExit(sl_pips=150, exit_indicator="H1_rsi_14",
                      exit_threshold=70, exit_direction="above", pip_size=pip_size),
        HybridExit(sl_pips=150, breakeven_activation_pips=50,
                   trail_distance_pips=100, max_candles=12, pip_size=pip_size),
        HybridExit(sl_pips=150, breakeven_activation_pips=100,
                   trail_distance_pips=200, max_candles=24, pip_size=pip_size),
    ]

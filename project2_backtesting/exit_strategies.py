"""
EXIT STRATEGIES — Pluggable exit strategy implementations.
Each strategy decides when to close a position based on price action.
Used by the strategy backtester to test different exit approaches.
"""
# WHY: Used by _check_management_blocked for min-hold time comparisons.
# CHANGED: April 2026 — min hold parity with MT5 EA
import pandas as pd
import random


# WHY (May 2026 — diagnostics): One-shot warning when M1 sub-candle
#      data isn't available but the parent candle DID reach SL/TP.
#      Without this, users assume the M1 fix is running when in
#      reality the backtest is falling back to parent-OHLC.
# CHANGED: May 2026 — one-shot M1-unavailable warning
_m1_fallback_warned = False
def _warn_m1_fallback_once():
    global _m1_fallback_warned
    if not _m1_fallback_warned:
        _m1_fallback_warned = True
        try:
            import logging as _log
            _log.getLogger(__name__).warning(
                "[M1] M1 sub-candle loader unavailable for one or more "
                "trades — falling back to parent-candle SL/TP detection. "
                "H4/D1 results may diverge from MT5. Check that M1 CSV "
                "exists and is real data (not an LFS stub)."
            )
        except Exception:
            pass


# WHY (May 2026): When a single entry-TF candle's range covers BOTH
#      the TP and the SL line, the candle's OHLC alone can't tell us
#      which was touched first. Default-returning TP (the prior
#      behavior) is systematically optimistic — MT5 tick reality
#      shows roughly 50/50 of these collisions actually go to SL.
#      Try M1 first (already loaded for trailing-stop ambiguity);
#      fall back to ticks if available; final fallback is pessimistic
#      (assume SL was hit first) which avoids the optimistic bias.
# CHANGED: May 2026 — same-candle TP/SL collision resolution
def _resolve_tp_sl_collision(pos, candle, tp_price, sl_price, direction):
    """Determine whether TP or SL was hit first within one candle.

    Returns 'TP' or 'SL'. Caller decides what to do with the answer.

    BUY: TP > entry > SL.   Iterate sub-candles in time order. The first
         sub-candle with high >= tp wins TP; the first with low <= sl
         wins SL.
    SELL: TP < entry < SL.  Mirror logic: low <= tp wins TP first;
         high >= sl wins SL first.

    If neither M1 nor ticks are available, returns 'SL' (pessimistic).
    """
    # Try ticks first (most accurate)
    _tick_loader = pos.get('_tick_loader')
    _ticks = _tick_loader(candle.get('timestamp')) if _tick_loader else None
    if _ticks is not None and len(_ticks) > 0:
        for _, t in _ticks.iterrows():
            # tick has 'bid' and 'ask'; use bid for BUY exit (price we'd
            # sell at to close), ask for SELL exit (price we'd buy at).
            try:
                p_for_buy_exit = float(t.get('bid', t.get('price', 0)))
                p_for_sell_exit = float(t.get('ask', t.get('price', 0)))
            except Exception:
                continue
            if direction == "BUY":
                if p_for_buy_exit <= sl_price: return 'SL'
                if p_for_buy_exit >= tp_price: return 'TP'
            else:  # SELL
                if p_for_sell_exit >= sl_price: return 'SL'
                if p_for_sell_exit <= tp_price: return 'TP'
        # Ticks ran out without crossing — shouldn't happen if the
        # parent candle showed a touch, but be safe
        return 'SL'

    # Fallback to M1 OHLC
    _m1_loader = pos.get('_m1_loader')
    _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
    if _m1_candles is not None and len(_m1_candles) > 0:
        for _, m1 in _m1_candles.iterrows():
            try:
                m1_high = float(m1['high'])
                m1_low  = float(m1['low'])
            except Exception:
                continue
            if direction == "BUY":
                m1_hit_sl = m1_low  <= sl_price
                m1_hit_tp = m1_high >= tp_price
            else:
                m1_hit_sl = m1_high >= sl_price
                m1_hit_tp = m1_low  <= tp_price
            if m1_hit_sl and m1_hit_tp:
                # Within ONE M1 candle both were hit. Pessimistic.
                return 'SL'
            if m1_hit_sl: return 'SL'
            if m1_hit_tp: return 'TP'
        # M1 didn't show a touch even though parent did — sub-candle
        # gap or rounding. Pessimistic.
        return 'SL'

    # No M1, no ticks — pessimistic default
    return 'SL'


# WHY (May 2026): Exit strategies that check SL by looking at the
#      parent candle's low/high miss intra-candle excursions. On H4
#      entries especially, a 1-minute tick spike that hits SL doesn't
#      always show in the recorded H4 candle low. Result: Python says
#      "TIME_EXIT win" while MT5 says "STOP_LOSS in minute 1." Use M1
#      sub-candles for intra-candle SL/TP detection — closes ~80% of
#      the gap vs the existing parent-candle-only check.
#
#      Falls back to the parent candle when:
#        - M1 loader isn't attached to pos (legacy callers)
#        - M1 data is missing for this symbol/period
#        - Parent candle's range already proves SL/TP wasn't touched
#          (no need to scan M1)
# CHANGED: May 2026 — M1 sub-candle SL/TP detection
def _check_sl_with_subcandles(candle, sl_price, direction, pos):
    """Check if SL was hit during this parent candle, using M1 if available.

    Returns (hit: bool, fill_price: float, hit_subcandle_ts).
      - hit: True if SL was touched
      - fill_price: the actual SL price (caller handles gap fills)
      - hit_subcandle_ts: timestamp of the M1 sub-candle that crossed,
        or the parent candle's timestamp when M1 fallback didn't run

    Pessimistic on M1-data gap: if parent candle's range crossed SL
    but no M1 sub-candle did, return True anyway (the parent OHLC is
    more authoritative than missing M1 detail).

    WHY (May 2026 — speed fix): Old version walked M1 candles via
         iterrows() — Python-level loop, ~10µs per row × up to 240
         rows per H4 parent = the dominant cost of the backtest.
         Replaced with numpy comparison on the full low/high array.
         Same logic, ~50-100× faster on H4/D1 rules.
    CHANGED: May 2026 — vectorized M1 scan
    """
    import numpy as _np
    # Fast-path: did the parent candle's range even reach SL?
    try:
        if direction == "BUY":
            parent_touched = float(candle["low"]) <= sl_price
        else:
            parent_touched = float(candle["high"]) >= sl_price
    except Exception:
        parent_touched = False

    if not parent_touched:
        # Parent says no — no need to scan M1
        return (False, sl_price, candle.get('timestamp') if hasattr(candle, 'get') else None)

    # Parent says yes. Find the EARLIEST M1 sub-candle where SL was hit.
    _m1_loader = pos.get('_m1_loader') if hasattr(pos, 'get') else None
    _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
    if _m1_candles is not None and len(_m1_candles) > 0:
        try:
            # Vectorize: get low/high as a numpy array once, then
            # do the comparison on the whole array at once.
            if direction == "BUY":
                _prices = _m1_candles['low'].to_numpy(dtype=float, copy=False)
                _hits   = _prices <= sl_price
            else:
                _prices = _m1_candles['high'].to_numpy(dtype=float, copy=False)
                _hits   = _prices >= sl_price

            if _hits.any():
                # np.argmax on a boolean array returns the first True index
                _hit_idx = int(_np.argmax(_hits))
                try:
                    _hit_ts = _m1_candles['timestamp'].iloc[_hit_idx]
                except Exception:
                    _hit_ts = candle.get('timestamp')
                return (True, sl_price, _hit_ts)
            # M1 didn't show a touch but parent did — sub-candle gap
            # or rounding. Pessimistic: assume SL was hit at parent's
            # timestamp. Matches the parent-OHLC view.
            return (True, sl_price, candle.get('timestamp'))
        except Exception:
            # On any numpy/pandas error, fall back to the parent
            # candle's authority — same behavior as if M1 wasn't
            # available at all.
            return (True, sl_price, candle.get('timestamp'))

    # No M1 loader / data available — trust the parent candle's range
    _warn_m1_fallback_once()
    return (True, sl_price, candle.get('timestamp'))


def _check_tp_with_subcandles(candle, tp_price, direction, pos):
    """Symmetric helper for TP detection. Same pattern as SL.

    Returns (hit: bool, fill_price: float, hit_subcandle_ts).

    WHY (May 2026 — speed fix): Vectorized for the same reason as
         _check_sl_with_subcandles. Same logic, no result changes.
    CHANGED: May 2026 — vectorized M1 scan
    """
    import numpy as _np
    try:
        if direction == "BUY":
            parent_touched = float(candle["high"]) >= tp_price
        else:
            parent_touched = float(candle["low"]) <= tp_price
    except Exception:
        parent_touched = False

    if not parent_touched:
        return (False, tp_price, candle.get('timestamp') if hasattr(candle, 'get') else None)

    _m1_loader = pos.get('_m1_loader') if hasattr(pos, 'get') else None
    _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
    if _m1_candles is not None and len(_m1_candles) > 0:
        try:
            if direction == "BUY":
                _prices = _m1_candles['high'].to_numpy(dtype=float, copy=False)
                _hits   = _prices >= tp_price
            else:
                _prices = _m1_candles['low'].to_numpy(dtype=float, copy=False)
                _hits   = _prices <= tp_price

            if _hits.any():
                _hit_idx = int(_np.argmax(_hits))
                try:
                    _hit_ts = _m1_candles['timestamp'].iloc[_hit_idx]
                except Exception:
                    _hit_ts = candle.get('timestamp')
                return (True, tp_price, _hit_ts)
            return (True, tp_price, candle.get('timestamp'))
        except Exception:
            return (True, tp_price, candle.get('timestamp'))

    _warn_m1_fallback_once()
    return (True, tp_price, candle.get('timestamp'))


# WHY (May 2026 — entry-candle gap fix): The main backtest loop starts
#      at ci=1, skipping the entry candle to avoid look-ahead bias on
#      trailing-stop strategies. But for fixed SL/TP (TimeBased,
#      FixedSLTP, etc.), the SL is placed at entry and CAN fire within
#      the entry candle's remaining minutes — MT5 catches these via
#      tick data and we missed them. This helper scans M1 sub-candles
#      strictly AFTER the entry timestamp to detect intra-entry-candle
#      SL/TP hits without violating look-ahead-bias rules.
# CHANGED: May 2026 — entry-candle SL/TP scan
def _check_entry_candle_sltp(entry_candle, entry_time, sl_price, tp_price,
                              direction, pos):
    """Scan M1 sub-candles from entry_time → end of entry candle.

    Returns one of:
      - None: neither SL nor TP touched after entry
      - ('SL', sl_price, hit_ts): SL touched first
      - ('TP', tp_price, hit_ts): TP touched first

    Same-M1-candle collision is resolved conservatively (SL wins) since
    we can't distinguish tick order from M1 OHLC alone.
    """
    import numpy as _np
    _m1_loader = pos.get('_m1_loader') if hasattr(pos, 'get') else None
    if _m1_loader is None:
        return None
    _m1 = _m1_loader(entry_candle.get('timestamp') if hasattr(entry_candle, 'get')
                     else entry_candle['timestamp'])
    if _m1 is None or len(_m1) == 0:
        return None

    try:
        import pandas as _pd
        _ets = _pd.Timestamp(entry_time)
        # Filter to M1 candles strictly AFTER entry. Equality (==) means the
        # M1 candle STARTED at entry — its bar contains pre-entry time too,
        # so we skip it to be safe. Use > not >=.
        _m1_after = _m1[_m1['timestamp'] > _ets]
        if len(_m1_after) == 0:
            return None

        if direction == "BUY":
            _sl_arr = _m1_after['low'].to_numpy(dtype=float, copy=False)
            _tp_arr = _m1_after['high'].to_numpy(dtype=float, copy=False)
            _sl_mask = _sl_arr <= sl_price if sl_price is not None else _np.zeros(len(_sl_arr), dtype=bool)
            _tp_mask = _tp_arr >= tp_price if tp_price is not None else _np.zeros(len(_tp_arr), dtype=bool)
        else:
            _sl_arr = _m1_after['high'].to_numpy(dtype=float, copy=False)
            _tp_arr = _m1_after['low'].to_numpy(dtype=float, copy=False)
            _sl_mask = _sl_arr >= sl_price if sl_price is not None else _np.zeros(len(_sl_arr), dtype=bool)
            _tp_mask = _tp_arr <= tp_price if tp_price is not None else _np.zeros(len(_tp_arr), dtype=bool)

        _sl_idx = int(_np.argmax(_sl_mask)) if _sl_mask.any() else -1
        _tp_idx = int(_np.argmax(_tp_mask)) if _tp_mask.any() else -1

        if _sl_idx < 0 and _tp_idx < 0:
            return None

        _ts_col = _m1_after['timestamp']

        if _sl_idx >= 0 and (_tp_idx < 0 or _sl_idx <= _tp_idx):
            return ('SL', sl_price, _ts_col.iloc[_sl_idx])
        else:
            return ('TP', tp_price, _ts_col.iloc[_tp_idx])
    except Exception:
        return None


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
        """DEPRECATED: Use _resolve_tp_sl_collision instead.

        Kept for backward compatibility with any external code.
        """
        return "SL"  # conservative fallback

    def _get_fill_price(self, candle, target_price, direction, is_sl=True):
        """
        Return actual fill price accounting for overnight/weekend gaps and SL slippage.

        WHY (May 2026): Real MT5 SL fills slip past the stop line by a median
             of 13 pips (mean 21, p95 50, max 140 on Get Leveraged). The old
             code filled at exact SL price unless candle gapped. Adding
             broker-specific slippage distribution (sampled deterministically
             per trade via timestamp seed) makes Python match MT5 reality.
        CHANGED: May 2026 — SL slippage distribution
        """
        candle_open = float(candle["open"])

        if is_sl:
            # Apply broker SL slippage distribution if configured
            slip_dist = getattr(self, 'sl_slippage_distribution', None)
            if slip_dist and slip_dist.get('samples'):
                ts = candle.get('timestamp')
                seed = int(pd.Timestamp(ts).value % (2**31)) if ts is not None else 0
                rng = random.Random(seed)
                slip_pips = rng.choice(slip_dist['samples'])
                slip_distance = slip_pips * self.pip_size
                if direction == "BUY":
                    target_price = target_price - slip_distance
                else:
                    target_price = target_price + slip_distance

            # Apply gap-open logic to (potentially slipped) sl_price
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

    @staticmethod
    def _normalize_price(price, pip_size):
        """Round price to pip precision, matching MT5's NormalizeDouble.

        WHY: MT5 normalizes all SL/TP prices to SYMBOL_DIGITS precision.
             Without this, floating-point dust (e.g. 4800.2500000001) can
             cause Python and MT5 to trigger differently on edge-case candles.
        CHANGED: April 2026 — price normalization for MT5 parity
        """
        if pip_size > 0:
            import math
            _decimals = max(0, -int(math.floor(math.log10(pip_size))))
            return round(price, _decimals)
        return price

    def _check_management_blocked(self, pos, candle):
        """Return True if still within the min-hold window.

        WHY: MT5 EA's MinHoldMinutes skips management actions (trail
             ratchet, breakeven lock, indicator exit) until the trade
             has been open for at least N minutes. Broker-level SL/TP
             still fires immediately. Without this, Python fires
             management exits within the same candle as entry.
        CHANGED: April 2026 — min hold parity with MT5 EA
        """
        _mhs = getattr(self, 'min_hold_seconds', 0)
        if not _mhs:
            return False
        try:
            _entry_dt = pd.Timestamp(pos.get("entry_time"))
            _now_dt   = pd.Timestamp(candle.get("timestamp"))
            return (_now_dt - _entry_dt).total_seconds() < _mhs
        except Exception:
            return False


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

        # WHY: Normalize to pip precision matching MT5's NormalizeDouble.
        # CHANGED: April 2026 — price normalization for MT5 parity
        # CHANGED: May 2026 — same-candle TP/SL collision resolution
        if direction == "BUY":
            sl_price = self._normalize_price(entry - self.sl_pips * self.pip_size, self.pip_size)
            tp_price = self._normalize_price(entry + self.tp_pips * self.pip_size, self.pip_size)
        else:  # SELL
            sl_price = self._normalize_price(entry + self.sl_pips * self.pip_size, self.pip_size)
            tp_price = self._normalize_price(entry - self.tp_pips * self.pip_size, self.pip_size)

        # WHY: Use M1 sub-candles for intra-candle SL/TP detection so
        #      H4/H1 rules don't miss tick spikes that MT5 sees.
        # CHANGED: May 2026 — M1 sub-candle SL/TP detection
        tp_touched, _, _ = _check_tp_with_subcandles(candle, tp_price, direction, pos)
        sl_touched, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)

        if tp_touched and sl_touched:
            # Same-candle collision — resolve via M1/ticks
            _which = _resolve_tp_sl_collision(
                pos, candle, tp_price, sl_price, direction
            )
            if _which == 'TP':
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "TAKE_PROFIT_GAP" if fill != tp_price else "TAKE_PROFIT"
                return {"exit_price": fill, "reason": reason}
            # else fall through to SL handling below

        elif tp_touched:
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "TAKE_PROFIT_GAP" if fill != tp_price else "TAKE_PROFIT"
            return {"exit_price": fill, "reason": reason}

        if sl_touched:
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
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
                 tp_pips=None, max_candles=None, pip_size=0.01,
                 # WHY: Gate trail ratchet during first N seconds. Broker SL/TP
                 #      still fires immediately. Matches EA's MinHoldMinutes.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0):
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
        self.tp_pips          = tp_pips
        self.max_candles      = max_candles
        self.min_hold_seconds = int(min_hold_seconds or 0)

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

        # WHY: Gate trail ratchet during min_hold window. Matches EA.
        # CHANGED: April 2026 — min hold parity
        _mgmt_blocked = self._check_management_blocked(pos, candle)

        if direction == "BUY":
            fixed_sl    = entry - self.sl_pips * self.pip_size
            profit_pips = (highest - entry) / self.pip_size

            # WHY (May 2026): Compute effective_sl FIRST so we can
            #      detect same-candle TP+SL collisions and resolve
            #      them by which level was actually hit first
            #      (M1/ticks). Previous code returned TP whenever
            #      candle.high reached TP, ignoring the case where
            #      candle.low also reached SL — systematic optimistic
            #      bias that drove most Python-vs-MT5 divergence.
            # CHANGED: May 2026 — same-candle TP/SL collision resolution
            if profit_pips >= self.activation_pips and not _mgmt_blocked:
                trail_sl     = highest - self.trail_distance_pips * self.pip_size
                effective_sl = max(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            tp_price = (entry + self.tp_pips * self.pip_size) if self.tp_pips is not None else None
            tp_touched = (tp_price is not None) and (candle["high"] >= tp_price)
            sl_touched = candle["low"] <= effective_sl

            if tp_touched and sl_touched:
                # Same-candle collision — resolve via M1/ticks
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, effective_sl, "BUY"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "TAKE_PROFIT"}
                # else fall through to SL handling below

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "TAKE_PROFIT"}

            if sl_touched:
                # WHY: Ambiguity — candle made a new high (trail advances) AND
                #      the low hit the trail. With ticks, determine which happened
                #      first. Without ticks the candle-based result stands.
                # CHANGED: April 2026 — tick-aware trailing ambiguity
                _new_high_this_candle = candle["high"] > pos.get("highest_since_entry", entry)
                _is_trailing = effective_sl > fixed_sl
                if _new_high_this_candle and _is_trailing:
                    _tick_loader = pos.get('_tick_loader')
                    _ticks = _tick_loader(candle.get('timestamp')) if _tick_loader else None
                    if _ticks is not None and len(_ticks) > 0:
                        _run_high  = pos.get("highest_since_entry", highest)
                        _trail_pip = self.trail_distance_pips * self.pip_size
                        for _, _tick in _ticks.iterrows():
                            try:
                                _bid = float(_tick['bid'])
                                if _bid > _run_high:
                                    _run_high = _bid
                                _tick_trail = _run_high - _trail_pip
                                _tick_eff   = max(fixed_sl, _tick_trail)
                                if _bid <= _tick_eff:
                                    _tr = _tick_eff > fixed_sl
                                    _reason = "TRAILING_STOP_TICK" if _tr else "STOP_LOSS_TICK"
                                    return {"exit_price": _tick_eff, "reason": _reason}
                            except Exception:
                                continue
                        # ticks ended without SL hit — try M1 before accepting candle result
                        _m1_loader = pos.get('_m1_loader')
                        _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
                        if _m1_candles is not None and len(_m1_candles) > 0:
                            # CHANGED: April 2026 — M1 fallback for BUY trailing ambiguity
                            _m1_run_high = pos.get("highest_since_entry", highest)
                            _trail_pip   = self.trail_distance_pips * self.pip_size
                            for _, _m1 in _m1_candles.iterrows():
                                try:
                                    _m1_high = float(_m1['high'])
                                    _m1_low  = float(_m1['low'])
                                    if _m1_high > _m1_run_high:
                                        _m1_run_high = _m1_high
                                    _m1_trail  = _m1_run_high - _trail_pip
                                    _m1_eff_sl = max(fixed_sl, _m1_trail)
                                    if _m1_low <= _m1_eff_sl:
                                        _tr = _m1_eff_sl > fixed_sl
                                        return {"exit_price": _m1_eff_sl,
                                                "reason": "TRAILING_STOP_M1" if _tr else "STOP_LOSS_M1"}
                                except Exception:
                                    continue
                        return None  # ticks + M1 ended without SL hit
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

            # WHY (May 2026): Same collision resolution as BUY, mirrored for SELL.
            # CHANGED: May 2026 — same-candle TP/SL collision resolution
            if profit_pips >= self.activation_pips and not _mgmt_blocked:
                trail_sl     = lowest + self.trail_distance_pips * self.pip_size
                effective_sl = min(fixed_sl, trail_sl)
            else:
                effective_sl = fixed_sl

            tp_price = (entry - self.tp_pips * self.pip_size) if self.tp_pips is not None else None
            tp_touched = (tp_price is not None) and (candle["low"] <= tp_price)
            sl_touched = candle["high"] >= effective_sl

            if tp_touched and sl_touched:
                # Same-candle collision — resolve via M1/ticks
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, effective_sl, "SELL"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "TAKE_PROFIT"}
                # else fall through to SL handling below

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "TAKE_PROFIT"}

            if sl_touched:
                # WHY: Tick-aware SELL trailing ambiguity — same as BUY.
                # CHANGED: April 2026 — tick-aware trailing ambiguity
                _new_low_this_candle = candle["low"] < pos.get("lowest_since_entry", entry)
                _is_trailing_sell = effective_sl < fixed_sl
                if _new_low_this_candle and _is_trailing_sell:
                    _tick_loader = pos.get('_tick_loader')
                    _ticks = _tick_loader(candle.get('timestamp')) if _tick_loader else None
                    if _ticks is not None and len(_ticks) > 0:
                        _run_low   = pos.get("lowest_since_entry", lowest)
                        _trail_pip = self.trail_distance_pips * self.pip_size
                        for _, _tick in _ticks.iterrows():
                            try:
                                _bid = float(_tick['bid'])
                                if _bid < _run_low:
                                    _run_low = _bid
                                _tick_trail = _run_low + _trail_pip
                                _tick_eff   = min(fixed_sl, _tick_trail)
                                if _bid >= _tick_eff:
                                    _tr = _tick_eff < fixed_sl
                                    _reason = "TRAILING_STOP_TICK" if _tr else "STOP_LOSS_TICK"
                                    return {"exit_price": _tick_eff, "reason": _reason}
                            except Exception:
                                continue
                        # ticks ended without SL hit — try M1 before accepting candle result
                        _m1_loader = pos.get('_m1_loader')
                        _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
                        if _m1_candles is not None and len(_m1_candles) > 0:
                            # CHANGED: April 2026 — M1 fallback for SELL trailing ambiguity
                            _m1_run_low = pos.get("lowest_since_entry", lowest)
                            _trail_pip  = self.trail_distance_pips * self.pip_size
                            for _, _m1 in _m1_candles.iterrows():
                                try:
                                    _m1_high = float(_m1['high'])
                                    _m1_low  = float(_m1['low'])
                                    if _m1_low < _m1_run_low:
                                        _m1_run_low = _m1_low
                                    _m1_trail  = _m1_run_low + _trail_pip
                                    _m1_eff_sl = min(fixed_sl, _m1_trail)
                                    if _m1_high >= _m1_eff_sl:
                                        _tr = _m1_eff_sl < fixed_sl
                                        return {"exit_price": _m1_eff_sl,
                                                "reason": "TRAILING_STOP_M1" if _tr else "STOP_LOSS_M1"}
                                except Exception:
                                    continue
                        return None  # ticks + M1 ended without SL hit
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
    # WHY: MT5's iATR uses Wilder's smoothing seeded with SMA(period).
    #      The non-MT5 H1_atr_14 column uses pandas.ewm() which seeds
    #      with the first value and diverges by 3-8% from MT5 native.
    #      Using mt5_atr_14 makes Python lot sizing and SL/TP match MT5.
    # CHANGED: April 2026 — switch to MT5-parity ATR column
    def __init__(self, sl_atr_mult=1.5, tp_atr_mult=3.0, atr_column="H1_mt5_atr_14",
                 max_candles=1000, entry_tf=None):
        super().__init__(sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult,
                         max_candles=max_candles)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        # CHANGED: June 2026 — guard against a saved atr_column of None/'' (which made
        #   candle.get(None) → _entry_atr None → junk 150/450-pip SL/TP). Fall back to
        #   the entry-TF MT5 ATR (matches the EA, which derives its ATR timeframe from
        #   this same column), else the legacy H1 default.
        if atr_column:
            self.atr_column = atr_column
        elif entry_tf:
            self.atr_column = f"{entry_tf}_mt5_atr_14"
        else:
            self.atr_column = "H1_mt5_atr_14"
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

        # WHY (May 2026): Same-candle TP/SL collision resolution.
        # CHANGED: May 2026 — realistic exit simulation parity
        if direction == "BUY":
            sl_price = entry - sl_distance
            tp_price = entry + tp_distance
        else:
            sl_price = entry + sl_distance
            tp_price = entry - tp_distance

        # WHY: M1 sub-candle SL/TP detection — see _check_sl_with_subcandles.
        # CHANGED: May 2026 — M1 sub-candle SL/TP detection
        tp_touched, _, _ = _check_tp_with_subcandles(candle, tp_price, direction, pos)
        sl_touched, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)

        if tp_touched and sl_touched:
            # Same-candle collision — resolve via M1/ticks
            _which = _resolve_tp_sl_collision(
                pos, candle, tp_price, sl_price, direction
            )
            if _which == 'TP':
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATR_TAKE_PROFIT_GAP" if fill != tp_price else "ATR_TAKE_PROFIT"
                return {"exit_price": fill, "reason": reason}
            # else fall through to SL handling below

        elif tp_touched:
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "ATR_TAKE_PROFIT_GAP" if fill != tp_price else "ATR_TAKE_PROFIT"
            return {"exit_price": fill, "reason": reason}

        if sl_touched:
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "ATR_STOP_LOSS_GAP" if fill != sl_price else "ATR_STOP_LOSS"
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

    def __init__(self, sl_atr_mult=1.0, tp_atr_mult=2.5, atr_column="H1_mt5_atr_14",
                 max_candles=200, pip_size=0.01, entry_tf=None):
        super().__init__(pip_size=pip_size, sl_atr_mult=sl_atr_mult,
                         tp_atr_mult=tp_atr_mult, max_candles=max_candles)
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        # CHANGED: June 2026 — guard against a saved atr_column of None/'' (which made
        #   candle.get(None) → _entry_atr None → junk 150/450-pip SL/TP). Fall back to
        #   the entry-TF MT5 ATR (matches the EA, which derives its ATR timeframe from
        #   this same column), else the legacy H1 default.
        if atr_column:
            self.atr_column = atr_column
        elif entry_tf:
            self.atr_column = f"{entry_tf}_mt5_atr_14"
        else:
            self.atr_column = "H1_mt5_atr_14"
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
        If ATR is missing or NaN, RESETS to safe defaults (150/300)
        instead of leaking stale values from the previous trade.
        """
        # WHY (May 2026 — Fix C): Reset to safe defaults BEFORE the ATR
        #      check. Without this, a previous trade's computed sl/tp
        #      values (e.g. sl=805, tp=1610 from ATR=4.025) silently
        #      leak into the next trade if the next entry's ATR is NaN.
        #      Strategy instances are reused across trades; on_entry
        #      must fully re-initialize per-trade state.
        # CHANGED: May 2026 — Fix C: prevent stale SL/TP state on ATR-NaN entries
        self.sl_pips = 150
        self.tp_pips = 300

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
                        f"invalid at entry. Reset to fail-safe defaults "
                        f"sl_pips=150, tp_pips=300. (Warning shown once per instance.)"
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

        # WHY: Normalize to pip precision matching MT5's NormalizeDouble.
        # CHANGED: April 2026 — price normalization for MT5 parity
        # CHANGED: May 2026 — same-candle TP/SL collision resolution
        if direction == "BUY":
            sl_price = self._normalize_price(entry - self.sl_pips * self.pip_size, self.pip_size)
            tp_price = self._normalize_price(entry + self.tp_pips * self.pip_size, self.pip_size)
        else:
            sl_price = self._normalize_price(entry + self.sl_pips * self.pip_size, self.pip_size)
            tp_price = self._normalize_price(entry - self.tp_pips * self.pip_size, self.pip_size)

        # WHY: M1 sub-candle SL/TP detection — see _check_sl_with_subcandles.
        # CHANGED: May 2026 — M1 sub-candle SL/TP detection
        tp_touched, _, _ = _check_tp_with_subcandles(candle, tp_price, direction, pos)
        sl_touched, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)

        if tp_touched and sl_touched:
            # Same-candle collision — resolve via M1/ticks
            _which = _resolve_tp_sl_collision(
                pos, candle, tp_price, sl_price, direction
            )
            if _which == 'TP':
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATR_FIXED_TP_GAP" if fill != tp_price else "ATR_FIXED_TP"
                return {"exit_price": fill, "reason": reason}
            # else fall through to SL handling below

        elif tp_touched:
            fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
            reason = "ATR_FIXED_TP_GAP" if fill != tp_price else "ATR_FIXED_TP"
            return {"exit_price": fill, "reason": reason}

        if sl_touched:
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "ATR_FIXED_SL_GAP" if fill != sl_price else "ATR_FIXED_SL"
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
                 tp_atr_mult=3.0, atr_column="H1_mt5_atr_14",
                 max_candles=200, pip_size=0.01,
                 # WHY: Gate BE lock and trail during min_hold window.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0, entry_tf=None):
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
        # CHANGED: June 2026 — guard against a saved atr_column of None/'' (which made
        #   candle.get(None) → _entry_atr None → junk 150/450-pip SL/TP). Fall back to
        #   the entry-TF MT5 ATR (matches the EA, which derives its ATR timeframe from
        #   this same column), else the legacy H1 default.
        if atr_column:
            self.atr_column = atr_column
        elif entry_tf:
            self.atr_column = f"{entry_tf}_mt5_atr_14"
        else:
            self.atr_column = "H1_mt5_atr_14"
        self.max_candles               = max_candles
        self.min_hold_seconds          = int(min_hold_seconds or 0)
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

        # WHY: Gate BE lock and trail during min_hold window. Matches EA.
        # CHANGED: April 2026 — min hold parity
        _mgmt_blocked = self._check_management_blocked(pos, candle)

        # WHY: Max hold cap — strongest guarantee against hanging.
        # CHANGED: April 2026 — max hold safety
        if self.max_candles is not None:
            if pos.get("candles_held", 0) >= self.max_candles:
                reason = "ATRBE_MAX_CANDLES"
                if self._entry_atr is None:
                    reason = "ATRBE_NO_DATA"
                return {"exit_price": float(candle["close"]), "reason": reason}

        if direction == "BUY":
            # WHY (May 2026): Check for TP/SL collision before returning TP.
            #      Compute effective SL based on current phase, then resolve
            #      collision if both levels touched in same candle.
            # CHANGED: May 2026 — same-candle TP/SL collision resolution
            tp_price = self._normalize_price(entry + self._tp_price_dist, self.pip_size)
            tp_touched = candle["high"] >= tp_price

            # Determine effective SL based on profit phase
            profit_from_entry = highest - entry
            if profit_from_entry >= self._trail_activation_price_dist and not _mgmt_blocked:
                # Phase 3: Trailing
                trail_sl = highest - self._trail_distance_price
                effective_sl = self._normalize_price(max(trail_sl, entry), self.pip_size)
            elif profit_from_entry >= self._be_distance_price and not _mgmt_blocked:
                # Phase 2: Breakeven
                effective_sl = entry
            else:
                # Phase 1: Initial ATR SL
                effective_sl = self._normalize_price(entry - self._sl_price_dist, self.pip_size)

            sl_touched = candle["low"] <= effective_sl

            if tp_touched and sl_touched:
                # Same-candle collision — resolve via M1/ticks
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, effective_sl, "BUY"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                    return {"exit_price": fill, "reason": reason}
                # else fall through to phase-specific SL handling below

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                return {"exit_price": fill, "reason": reason}

            # WHY: Intra-candle ambiguity — both initial SL and breakeven
            #      activation are within this candle's range. Use tick data
            #      to determine which was hit first. Falls back to conservative
            #      SL if no ticks available.
            # CHANGED: April 2026 — tick-aware breakeven ambiguity resolution
            _initial_sl_price = self._normalize_price(entry - self._sl_price_dist, self.pip_size)
            _be_threshold     = entry + self._be_distance_price
            _be_would_activate = candle["high"] >= _be_threshold
            _sl_would_hit      = candle["low"]  <= _initial_sl_price
            if _be_would_activate and _sl_would_hit and not self._breakeven_locked:
                _tick_loader = pos.get('_tick_loader')
                _ticks = _tick_loader(candle.get('timestamp')) if _tick_loader else None
                if _ticks is not None and len(_ticks) > 0:
                    _highest_bid = entry
                    _be_activated = False
                    for _, _tick in _ticks.iterrows():
                        try:
                            _bid = float(_tick['bid'])
                            if _bid > _highest_bid:
                                _highest_bid = _bid
                            if _bid <= _initial_sl_price:
                                # SL hit first
                                return {"exit_price": _initial_sl_price, "reason": "ATRBE_SL_TICK"}
                            if _highest_bid >= _be_threshold:
                                _be_activated = True
                                break  # breakeven activated first — check for exit below
                        except Exception:
                            continue
                    if _be_activated:
                        self._breakeven_locked = True
                        # Check if bid then pulled back to entry (breakeven exit)
                        for _, _tick in _ticks.iterrows():
                            try:
                                if float(_tick['bid']) >= _be_threshold:
                                    break  # scan from breakeven activation point
                            except Exception:
                                continue
                        # fall through — let phase logic handle rest of candle
                    # else: ticks ended without either — no exit, fall through
                else:
                    # No tick data — try M1 sub-candles
                    # WHY: M1 gives 60 data points per H1 bar — resolves
                    #      most ambiguity without the storage cost of ticks.
                    # CHANGED: April 2026 — M1 fallback for BUY breakeven ambiguity
                    _m1_loader = pos.get('_m1_loader')
                    _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
                    if _m1_candles is not None and len(_m1_candles) > 0:
                        _m1_highest = entry
                        for _, _m1 in _m1_candles.iterrows():
                            try:
                                _m1_high = float(_m1['high'])
                                _m1_low  = float(_m1['low'])
                                if _m1_high > _m1_highest:
                                    _m1_highest = _m1_high
                                if _m1_low <= _initial_sl_price:
                                    _m1c = {'open': float(_m1['open']), 'high': _m1_high,
                                            'low': _m1_low, 'close': float(_m1['close'])}
                                    fill = self._get_fill_price(_m1c, _initial_sl_price, direction, is_sl=True)
                                    return {"exit_price": fill, "reason": "ATRBE_SL_M1"}
                                if (_m1_highest - entry) >= self._be_distance_price:
                                    self._breakeven_locked = True
                                    if _m1_low <= entry:
                                        return {"exit_price": entry, "reason": "ATRBE_BREAKEVEN_M1"}
                                    break  # breakeven locked — let phase logic handle rest
                            except Exception:
                                continue
                        # fall through to phase logic (breakeven may be locked)
                    else:
                        # No M1 either — conservative: assume SL hit first
                        fill = self._get_fill_price(candle, _initial_sl_price, direction, is_sl=True)
                        return {"exit_price": fill, "reason": "ATRBE_SL_AMBIGUOUS"}

            # Phase 3: Trailing (activates after trail_activation distance)
            # WHY: Gated by min hold — trail ratchet is a management action.
            # CHANGED: April 2026 — min hold parity
            if profit_from_entry >= self._trail_activation_price_dist and not _mgmt_blocked:
                trail_sl = highest - self._trail_distance_price
                # WHY: Trail SL must be at least at breakeven. Never
                #      trail BELOW entry once breakeven was reached.
                # CHANGED: April 2026 — floor trail at entry
                trail_sl = max(trail_sl, entry)
                if candle["low"] <= trail_sl:
                    fill = self._get_fill_price(candle, trail_sl, direction, is_sl=True)
                    reason = "ATRBE_TRAIL_GAP" if fill != trail_sl else "ATRBE_TRAIL"
                    return {"exit_price": fill, "reason": reason}

            # Phase 2: Breakeven lock (also gated — management action)
            elif profit_from_entry >= self._be_distance_price and not _mgmt_blocked:
                self._breakeven_locked = True
                if candle["low"] <= entry:
                    fill = self._get_fill_price(candle, entry, direction, is_sl=True)
                    reason = "ATRBE_BREAKEVEN_GAP" if fill != entry else "ATRBE_BREAKEVEN"
                    return {"exit_price": fill, "reason": reason}

            # Phase 1: Initial ATR SL
            else:
                sl_price = self._normalize_price(entry - self._sl_price_dist, self.pip_size)
                # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
                # CHANGED: May 2026 — M1 sub-candle SL detection
                _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
                if _sl_hit:
                    fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                    reason = "ATRBE_SL_GAP" if fill != sl_price else "ATRBE_SL"
                    return {"exit_price": fill, "reason": reason}

        else:  # SELL
            # WHY (May 2026): Same collision resolution for SELL.
            # CHANGED: May 2026 — same-candle TP/SL collision resolution
            tp_price = self._normalize_price(entry - self._tp_price_dist, self.pip_size)
            tp_touched = candle["low"] <= tp_price

            # Determine effective SL based on profit phase
            profit_from_entry = entry - lowest
            if profit_from_entry >= self._trail_activation_price_dist and not _mgmt_blocked:
                # Phase 3: Trailing
                trail_sl = lowest + self._trail_distance_price
                effective_sl = self._normalize_price(min(trail_sl, entry), self.pip_size)
            elif profit_from_entry >= self._be_distance_price and not _mgmt_blocked:
                # Phase 2: Breakeven
                effective_sl = entry
            else:
                # Phase 1: Initial ATR SL
                effective_sl = self._normalize_price(entry + self._sl_price_dist, self.pip_size)

            sl_touched = candle["high"] >= effective_sl

            if tp_touched and sl_touched:
                # Same-candle collision — resolve via M1/ticks
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, effective_sl, "SELL"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                    return {"exit_price": fill, "reason": reason}
                # else fall through to phase-specific SL handling below

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "ATRBE_TP_GAP" if fill != tp_price else "ATRBE_TP"
                return {"exit_price": fill, "reason": reason}

            # WHY: Tick-aware breakeven ambiguity resolution for SELL.
            # CHANGED: April 2026 — tick-aware breakeven ambiguity resolution
            _initial_sl_price = self._normalize_price(entry + self._sl_price_dist, self.pip_size)
            _be_threshold_sell = entry - self._be_distance_price
            _be_would_activate = candle["low"]  <= _be_threshold_sell
            _sl_would_hit      = candle["high"] >= _initial_sl_price
            if _be_would_activate and _sl_would_hit and not self._breakeven_locked:
                _tick_loader = pos.get('_tick_loader')
                _ticks = _tick_loader(candle.get('timestamp')) if _tick_loader else None
                if _ticks is not None and len(_ticks) > 0:
                    _lowest_bid = entry
                    _be_activated_sell = False
                    for _, _tick in _ticks.iterrows():
                        try:
                            _bid = float(_tick['bid'])
                            if _bid < _lowest_bid:
                                _lowest_bid = _bid
                            if _bid >= _initial_sl_price:
                                return {"exit_price": _initial_sl_price, "reason": "ATRBE_SL_TICK"}
                            if _lowest_bid <= _be_threshold_sell:
                                _be_activated_sell = True
                                break
                        except Exception:
                            continue
                    if _be_activated_sell:
                        self._breakeven_locked = True
                else:
                    # No tick data — try M1 sub-candles (SELL)
                    # CHANGED: April 2026 — M1 fallback for SELL breakeven ambiguity
                    _m1_loader = pos.get('_m1_loader')
                    _m1_candles = _m1_loader(candle.get('timestamp')) if _m1_loader else None
                    if _m1_candles is not None and len(_m1_candles) > 0:
                        _m1_lowest = entry
                        for _, _m1 in _m1_candles.iterrows():
                            try:
                                _m1_high = float(_m1['high'])
                                _m1_low  = float(_m1['low'])
                                if _m1_low < _m1_lowest:
                                    _m1_lowest = _m1_low
                                if _m1_high >= _initial_sl_price:
                                    _m1c = {'open': float(_m1['open']), 'high': _m1_high,
                                            'low': _m1_low, 'close': float(_m1['close'])}
                                    fill = self._get_fill_price(_m1c, _initial_sl_price, direction, is_sl=True)
                                    return {"exit_price": fill, "reason": "ATRBE_SL_M1"}
                                if (entry - _m1_lowest) >= self._be_distance_price:
                                    self._breakeven_locked = True
                                    if _m1_high >= entry:
                                        return {"exit_price": entry, "reason": "ATRBE_BREAKEVEN_M1"}
                                    break
                            except Exception:
                                continue
                    else:
                        fill = self._get_fill_price(candle, _initial_sl_price, direction, is_sl=True)
                        return {"exit_price": fill, "reason": "ATRBE_SL_AMBIGUOUS"}

            # Phase 3: Trailing (gated)
            if profit_from_entry >= self._trail_activation_price_dist and not _mgmt_blocked:
                trail_sl = lowest + self._trail_distance_price
                trail_sl = min(trail_sl, entry)  # floor at breakeven
                if candle["high"] >= trail_sl:
                    fill = self._get_fill_price(candle, trail_sl, direction, is_sl=True)
                    reason = "ATRBE_TRAIL_GAP" if fill != trail_sl else "ATRBE_TRAIL"
                    return {"exit_price": fill, "reason": reason}

            # Phase 2: Breakeven (gated)
            elif profit_from_entry >= self._be_distance_price and not _mgmt_blocked:
                self._breakeven_locked = True
                if candle["high"] >= entry:
                    fill = self._get_fill_price(candle, entry, direction, is_sl=True)
                    reason = "ATRBE_BREAKEVEN_GAP" if fill != entry else "ATRBE_BREAKEVEN"
                    return {"exit_price": fill, "reason": reason}

            # Phase 1: Initial SL
            else:
                sl_price = self._normalize_price(entry + self._sl_price_dist, self.pip_size)
                # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
                # CHANGED: May 2026 — M1 sub-candle SL detection
                _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
                if _sl_hit:
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
                 atr_column="H1_mt5_atr_14",
                 psar_signal_column="H1_psar_signal",
                 min_candles_before_psar=2,
                 max_candles=200, pip_size=0.01,
                 # WHY: Gate PSAR flip exit during min_hold window. Matches EA.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0,
                 entry_tf=None):
        super().__init__(pip_size=pip_size,
                         sl_atr_mult=sl_atr_mult,
                         tp_atr_mult=tp_atr_mult,
                         max_candles=max_candles)
        self.sl_atr_mult          = sl_atr_mult
        self.tp_atr_mult          = tp_atr_mult
        # CHANGED: June 2026 — PSAR exit must read the ENTRY-TF PSAR (MT5 uses iSAR on the
        #   entry chart). Reading H1_psar_signal for an M5 rule meant the flip exit never
        #   fired → trades rode to SL/TP (255) instead of flipping out at ~10 min like MT5
        #   (PSARFlipExit, median 10 min). Default both columns to entry_tf; keep any
        #   explicit non-null, non-H1-default saved value; fall back to H1 only if unknown.
        #   The != "H1_..." guards ensure a stored hardcoded-H1 default for a non-H1 rule
        #   is corrected to the entry TF on reconstruction via _build_exit_strategy.
        if psar_signal_column and psar_signal_column != "H1_psar_signal":
            self.psar_signal_column = psar_signal_column
        elif entry_tf:
            self.psar_signal_column = f"{entry_tf}_psar_signal"
        else:
            self.psar_signal_column = psar_signal_column or "H1_psar_signal"
        if atr_column and atr_column != "H1_mt5_atr_14":
            self.atr_column = atr_column
        elif entry_tf:
            self.atr_column = f"{entry_tf}_mt5_atr_14"
        else:
            self.atr_column = atr_column or "H1_mt5_atr_14"
        # WHY: Skip PSAR check for the first N candles after entry.
        #      PSAR sometimes hasn't "caught up" to a new entry yet —
        #      it can still be flipped against the trade from the prior
        #      move. Giving it 2 candles to settle avoids false exits
        #      on the very first bar.
        # CHANGED: April 2026 — min candles before PSAR check
        self.min_candles_before_psar = min_candles_before_psar
        self.max_candles             = max_candles
        self.min_hold_seconds        = int(min_hold_seconds or 0)
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
                    # CHANGED: June 2026 — make the missing-ATR fallback unmistakable in logs
                    _log.warning(
                        "[PSARExit] entry ATR missing (atr_column=%r) — using junk "
                        "150/450-pip SL/TP. Trades will NOT hit realistic stops. "
                        "Check the rule's atr_column. (Warning shown once.)",
                        self.atr_column
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
            # WHY: Normalize to pip precision matching MT5's NormalizeDouble.
            # CHANGED: April 2026 — price normalization for MT5 parity
            tp_price = self._normalize_price(entry + self._tp_price_dist, self.pip_size)
            if candle["high"] >= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "PSAR_TP_GAP" if fill != tp_price else "PSAR_TP"
                return {"exit_price": fill, "reason": reason}

            # ATR SL (safety net — always active)
            sl_price = self._normalize_price(entry - self._sl_price_dist, self.pip_size)
            # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
            # CHANGED: May 2026 — M1 sub-candle SL detection
            _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
            if _sl_hit:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "PSAR_SL_GAP" if fill != sl_price else "PSAR_SL"
                return {"exit_price": fill, "reason": reason}

            # PSAR flip check — gated by min hold (management action)
            # WHY: CHANGED: April 2026 — min hold parity
            _psar_mgmt_blocked = self._check_management_blocked(pos, candle)
            if (pos.get("candles_held", 0) >= self.min_candles_before_psar
                    and not _psar_mgmt_blocked):
                psar_signal = candle.get(self.psar_signal_column)
                if psar_signal is not None:
                    try:
                        # WHY: BUY exit when PSAR flips bearish (signal = 0.0).
                        #      psar_signal: 1.0 = bullish, 0.0 = bearish.
                        # CHANGED: April 2026 — PSAR flip detection
                        # CHANGED: June 2026 — Fix 1: next-bar-open fill (live-realistic).
                        #   Fix 2: M1 sub-candle refinement to match MT5 intrabar exit.
                        if float(psar_signal) == 0.0:
                            # Fix 2: try M1 sub-candle resolution first
                            _psar_col = self.psar_signal_column.replace('_psar_signal', '_psar')
                            _psar_price = candle.get(_psar_col)
                            _m1_loader = pos.get('_m1_loader') if hasattr(pos, 'get') else None
                            _subs = (_m1_loader(candle.get('timestamp'))
                                     if (_m1_loader and _psar_price is not None) else None)
                            if _subs is not None and len(_subs) > 0:
                                try:
                                    import numpy as _np
                                    _cl = _subs['close'].to_numpy(dtype=float, copy=False)
                                    _hits = _cl < float(_psar_price)  # BUY flip = close below PSAR
                                    if _hits.any():
                                        _i = int(_np.argmax(_hits))
                                        _fill = (float(_subs['open'].iloc[_i + 1])
                                                 if _i + 1 < len(_subs)
                                                 else float(_subs['close'].iloc[_i]))
                                        return {"exit_price": _fill, "reason": "PSAR_FLIP"}
                                except Exception:
                                    pass
                            # Fix 1 fallback: next parent-bar open (or close if unavailable)
                            _fill = candle.get("next_open") or candle["close"]
                            return {"exit_price": float(_fill), "reason": "PSAR_FLIP"}
                    except (TypeError, ValueError):
                        pass

        else:  # SELL
            # Hard TP
            tp_price = self._normalize_price(entry - self._tp_price_dist, self.pip_size)
            if candle["low"] <= tp_price:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                reason = "PSAR_TP_GAP" if fill != tp_price else "PSAR_TP"
                return {"exit_price": fill, "reason": reason}

            # ATR SL
            sl_price = self._normalize_price(entry + self._sl_price_dist, self.pip_size)
            # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
            # CHANGED: May 2026 — M1 sub-candle SL detection
            _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
            if _sl_hit:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                reason = "PSAR_SL_GAP" if fill != sl_price else "PSAR_SL"
                return {"exit_price": fill, "reason": reason}

            # PSAR flip check — gated by min hold
            if (pos.get("candles_held", 0) >= self.min_candles_before_psar
                    and not _psar_mgmt_blocked):
                psar_signal = candle.get(self.psar_signal_column)
                if psar_signal is not None:
                    try:
                        # WHY: SELL exit when PSAR flips bullish (signal = 1.0).
                        # CHANGED: April 2026 — PSAR flip detection
                        # CHANGED: June 2026 — Fix 1 + Fix 2 (mirror of BUY block above)
                        if float(psar_signal) == 1.0:
                            _psar_col = self.psar_signal_column.replace('_psar_signal', '_psar')
                            _psar_price = candle.get(_psar_col)
                            _m1_loader = pos.get('_m1_loader') if hasattr(pos, 'get') else None
                            _subs = (_m1_loader(candle.get('timestamp'))
                                     if (_m1_loader and _psar_price is not None) else None)
                            if _subs is not None and len(_subs) > 0:
                                try:
                                    import numpy as _np
                                    _cl = _subs['close'].to_numpy(dtype=float, copy=False)
                                    _hits = _cl > float(_psar_price)  # SELL flip = close above PSAR
                                    if _hits.any():
                                        _i = int(_np.argmax(_hits))
                                        _fill = (float(_subs['open'].iloc[_i + 1])
                                                 if _i + 1 < len(_subs)
                                                 else float(_subs['close'].iloc[_i]))
                                        return {"exit_price": _fill, "reason": "PSAR_FLIP"}
                                except Exception:
                                    pass
                            _fill = candle.get("next_open") or candle["close"]
                            return {"exit_price": float(_fill), "reason": "PSAR_FLIP"}
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

    def __init__(self, sl_atr_mult=2.0, tp_atr_mult=4.0, atr_column="H1_mt5_atr_14",
                 activation_pips=50, trail_distance_pips=100,
                 max_candles=1000, pip_size=0.01,
                 # WHY: Gate trail ratchet during min_hold window. Matches EA.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0,
                 entry_tf=None):
        super().__init__(sl_atr_mult=sl_atr_mult, tp_atr_mult=tp_atr_mult,
                         activation_pips=activation_pips,
                         trail_distance_pips=trail_distance_pips,
                         max_candles=max_candles, pip_size=pip_size)
        self.sl_atr_mult         = sl_atr_mult
        self.tp_atr_mult         = tp_atr_mult
        # CHANGED: June 2026 — guard atr_column; default to entry TF (EA parity), not H1
        if atr_column:
            self.atr_column = atr_column
        elif entry_tf:
            self.atr_column = f"{entry_tf}_mt5_atr_14"
        else:
            self.atr_column = "H1_mt5_atr_14"
        self.activation_pips     = activation_pips
        self.trail_distance_pips = trail_distance_pips
        self.max_candles         = max_candles
        self.pip_size            = pip_size
        self.min_hold_seconds    = int(min_hold_seconds or 0)
        self._entry_atr          = None

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

        # WHY: Gate trail ratchet during min_hold window. Matches EA.
        # CHANGED: April 2026 — min hold parity
        _trail_mgmt_blocked = self._check_management_blocked(pos, candle)

        # WHY (May 2026): Same-candle TP/SL collision resolution.
        # CHANGED: May 2026 — realistic exit simulation parity
        if direction == "BUY":
            sl_price = entry - sl_distance
            tp_price = entry + tp_distance

            profit_pips = (highest - entry) / self.pip_size
            if profit_pips >= self.activation_pips and not _trail_mgmt_blocked:
                trail_sl = highest - self.trail_distance_pips * self.pip_size
                if trail_sl > sl_price:
                    sl_price = trail_sl

            tp_touched = candle["high"] >= tp_price
            # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
            # CHANGED: May 2026 — M1 sub-candle SL detection
            sl_touched, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)

            if tp_touched and sl_touched:
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, sl_price, "BUY"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}
                # else fall through to SL

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}

            if sl_touched:
                fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
                return {"exit_price": fill, "reason": "ATR_TRAIL_SL"}

        else:
            sl_price = entry + sl_distance
            tp_price = entry - tp_distance

            profit_pips = (entry - lowest) / self.pip_size
            if profit_pips >= self.activation_pips and not _trail_mgmt_blocked:
                trail_sl = lowest + self.trail_distance_pips * self.pip_size
                if trail_sl < sl_price:
                    sl_price = trail_sl

            tp_touched = candle["low"] <= tp_price
            # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
            # CHANGED: May 2026 — M1 sub-candle SL detection
            sl_touched, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)

            if tp_touched and sl_touched:
                _which = _resolve_tp_sl_collision(
                    pos, candle, tp_price, sl_price, "SELL"
                )
                if _which == 'TP':
                    fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                    return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}
                # else fall through to SL

            elif tp_touched:
                fill = self._get_fill_price(candle, tp_price, direction, is_sl=False)
                return {"exit_price": fill, "reason": "ATR_TRAIL_TP"}

            if sl_touched:
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
        else:
            sl_price = entry + self.sl_pips * self.pip_size

        # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
        #      Bug we traced: H4 entry rules with TimeBased exits showed
        #      Python TIME_EXIT wins where MT5 had STOP_LOSS at minute 1.
        #      Root cause was here.
        # CHANGED: May 2026 — M1 sub-candle SL detection
        _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
        if _sl_hit:
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

    # WHY: Default to MT5-parity RSI for consistency with other exits.
    # CHANGED: April 2026 — MT5-parity RSI default
    def __init__(self, sl_pips=150, exit_indicator="H1_mt5_rsi_14",
                 exit_threshold=70, exit_direction="above",
                 max_candles=500, pip_size=0.01,
                 # WHY: Gate indicator-based exit during min_hold window.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0,
                 entry_tf=None):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         exit_indicator=exit_indicator, exit_threshold=exit_threshold,
                         max_candles=max_candles)
        self.sl_pips          = sl_pips
        # CHANGED: June 2026 — guard exit_indicator; default its TF to the entry TF.
        #   Keeps a valid saved indicator; only fills when missing.
        if exit_indicator:
            self.exit_indicator = exit_indicator
        elif entry_tf:
            self.exit_indicator = f"{entry_tf}_mt5_rsi_14"
        else:
            self.exit_indicator = "H1_mt5_rsi_14"
        self.exit_threshold   = exit_threshold
        self.exit_direction   = exit_direction
        self.min_hold_seconds = int(min_hold_seconds or 0)
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
        else:
            sl_price = entry + self.sl_pips * self.pip_size

        # WHY: M1 sub-candle SL detection — see _check_sl_with_subcandles.
        # CHANGED: May 2026 — M1 sub-candle SL detection
        _sl_hit, _, _ = _check_sl_with_subcandles(candle, sl_price, direction, pos)
        if _sl_hit:
            fill = self._get_fill_price(candle, sl_price, direction, is_sl=True)
            reason = "STOP_LOSS_GAP" if fill != sl_price else "STOP_LOSS"
            return {"exit_price": fill, "reason": reason}

        # WHY: Gate indicator exit during min_hold window. Matches EA.
        # CHANGED: April 2026 — min hold parity
        if pos["candles_held"] >= 1 and not self._check_management_blocked(pos, candle):
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
                 trail_distance_pips=100, max_candles=12, pip_size=0.01,
                 # WHY: Gate BE move and trail during min_hold window.
                 # CHANGED: April 2026 — min hold parity
                 min_hold_seconds=0):
        super().__init__(pip_size=pip_size, sl_pips=sl_pips,
                         breakeven_activation_pips=breakeven_activation_pips,
                         trail_distance_pips=trail_distance_pips,
                         max_candles=max_candles)
        self.sl_pips          = sl_pips
        self.breakeven_pips   = breakeven_activation_pips
        self.trail_pips       = trail_distance_pips
        self.max_candles      = max_candles
        self.min_hold_seconds = int(min_hold_seconds or 0)

    def on_new_candle(self, candle, pos):
        entry     = pos["entry_price"]
        direction = pos["direction"]
        highest   = pos["highest_since_entry"]
        lowest    = pos["lowest_since_entry"]

        # WHY: Gate BE move and trail during min_hold window. Matches EA.
        # CHANGED: April 2026 — min hold parity
        _hybrid_mgmt_blocked = self._check_management_blocked(pos, candle)

        if direction == "BUY":
            fixed_sl    = entry - self.sl_pips * self.pip_size
            profit_pips = (highest - entry) / self.pip_size

            if profit_pips >= self.breakeven_pips and not _hybrid_mgmt_blocked:
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

            if profit_pips >= self.breakeven_pips and not _hybrid_mgmt_blocked:
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
    # WHY: MT5 parity — use mt5_atr_14 which matches MT5 iATR's Wilder+SMA
    #      seeding behavior. See comment in ATRBased.__init__.
    # CHANGED: April 2026 — MT5-parity ATR for all default exit instances
    _atr_col = f"{entry_tf}_mt5_atr_14" if entry_tf else "H1_mt5_atr_14"
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
                 atr_column=_atr_col, max_candles=200, pip_size=pip_size,
                 entry_tf=entry_tf),
        PSARExit(sl_atr_mult=1.0, tp_atr_mult=3.0,
                 psar_signal_column=f"{entry_tf}_psar_signal" if entry_tf else "H1_psar_signal",
                 atr_column=_atr_col, min_candles_before_psar=3,
                 max_candles=150, pip_size=pip_size,
                 entry_tf=entry_tf),
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
        # WHY: MT5 parity — H1_rsi_14 (ta library) diverges from MT5 iRSI.
        #      H1_mt5_rsi_14 uses Wilder's smoothing seeded with SMA(period).
        # CHANGED: April 2026 — MT5-parity RSI for IndicatorExit
        # CHANGED: June 2026 — use entry TF for RSI column (was hardcoded H1)
        IndicatorExit(sl_pips=150,
                      exit_indicator=f"{entry_tf}_mt5_rsi_14" if entry_tf else "H1_mt5_rsi_14",
                      exit_threshold=70, exit_direction="above", pip_size=pip_size,
                      entry_tf=entry_tf),
        HybridExit(sl_pips=150, breakeven_activation_pips=50,
                   trail_distance_pips=100, max_candles=12, pip_size=pip_size),
        HybridExit(sl_pips=150, breakeven_activation_pips=100,
                   trail_distance_pips=200, max_candles=24, pip_size=pip_size),
    ]

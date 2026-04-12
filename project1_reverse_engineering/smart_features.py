"""
Smart Features — computed from existing indicators and candle data.

These features capture:
- Inter-timeframe relationships (divergences, alignment)
- Indicator dynamics (direction, acceleration, regime changes)
- Time intelligence (session phases, calendar events)
- Price action patterns (candle sequences, key levels)
- Momentum quality

All features are added as new columns to the feature matrix.
Results cached to outputs/smart_feature_matrix.csv.
"""

import os
import sys
import numpy as np
import pandas as pd

# Add parent dir to sys.path for shared imports
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')
CACHE_PATH = os.path.join(OUTPUT_DIR, 'smart_feature_matrix.csv')


def compute_smart_features(feature_matrix_path=None, force_recompute=False,
                            progress_callback=None):
    """
    Compute smart features and return extended DataFrame.
    Caches to smart_feature_matrix.csv.

    Returns: DataFrame with original columns + ~50 SMART_ + 14 REGIME_ columns.
    """
    # Phase 43 Fix 5: invalidate cache if input feature_matrix is newer
    if not force_recompute and os.path.exists(CACHE_PATH):
        cached_df = pd.read_csv(CACHE_PATH)
        smart_cols = [c for c in cached_df.columns if c.startswith('SMART_')]
        _cache_valid = False
        if len(smart_cols) > 30:
            try:
                _cache_mtime = os.path.getmtime(CACHE_PATH)
                _input_path = feature_matrix_path if feature_matrix_path else os.path.join(OUTPUT_DIR, 'feature_matrix.csv')
                _input_mtime = os.path.getmtime(_input_path) if os.path.exists(_input_path) else 0
                _cache_valid = _cache_mtime >= _input_mtime
                if not _cache_valid:
                    from shared.logging_setup import get_logger
                    get_logger(__name__).warning(
                        f"[SMART_FEATURES] Cache at {CACHE_PATH} is older than "
                        f"input feature matrix — invalidating."
                    )
            except Exception:
                _cache_valid = False
        if _cache_valid:
            return cached_df

    if feature_matrix_path is None:
        feature_matrix_path = os.path.join(OUTPUT_DIR, 'feature_matrix.csv')

    df = pd.read_csv(feature_matrix_path)

    # Check feature toggles
    try:
        from shared import feature_toggles
        smart_enabled  = feature_toggles.get_smart()
        # WHY (Phase 43 Fix 5): Old cache check was just os.path.exists +
        #      column count > 30. No mtime comparison vs the input
        #      feature_matrix. User re-runs step1/step2 with new data,
        #      old SMART cache is reused, and stale smart features get
        #      merged with new raw features → garbage rules. Mtime
        #      check below.
        # CHANGED: April 2026 — Phase 43 Fix 5 — cache mtime guard
        #          (audit Part D HIGH #16)
        regime_enabled = feature_toggles.get_regime()
    except ImportError:
        # WHY (Phase 43 Fix 8): Old code silently force-enabled both
        #      smart_enabled and regime_enabled when feature_toggles
        #      was missing, overriding any user intent expressed
        #      elsewhere. Log a one-shot warning so the user knows
        #      the toggle module isn't being consulted.
        # CHANGED: April 2026 — Phase 43 Fix 8 — visible toggle fallback
        #          (audit Part D MED #24)
        try:
            from shared.logging_setup import get_logger
            get_logger(__name__).warning(
                "[SMART_FEATURES] feature_toggles module not importable — "
                "force-enabling smart_enabled and regime_enabled. Create "
                "feature_toggles.py to control these explicitly."
            )
        except Exception:
            pass
        smart_enabled = True
        regime_enabled = True

    total_steps = 8 + (1 if regime_enabled else 0)
    step = 0

    if smart_enabled:
        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing TF divergences...")
        df = _add_tf_divergences(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing indicator dynamics...")
        df = _add_indicator_dynamics(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing TF alignment...")
        df = _add_alignment_scores(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing session features...")
        df = _add_session_intelligence(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing calendar features...")
        df = _add_calendar_features(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing volatility regimes...")
        df = _add_volatility_regimes(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing price action...")
        df = _add_price_action(df)

        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing momentum quality...")
        df = _add_momentum_quality(df)

    if regime_enabled:
        step += 1
        if progress_callback:
            progress_callback(step, total_steps, "Computing regime features...")
        df = _add_regime_features(df)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df.to_csv(CACHE_PATH, index=False)
    return df


# WHY (Phase 41 Fix 2): Old _safe_col returned zeros silently when a
#      column was missing. Every divergence/comparison feature
#      downstream got wrong semantics — SMART_rsi_h4_minus_h1 became
#      -h1_rsi when H4 wasn't loaded, fitted by ML as if real. Track
#      missing columns in a module-level set, log a one-shot warning
#      per column name so users know which features are faked.
#      Returns zeros for backward compat; downstream consumers can
#      check _missing_columns to filter fake features out of training.
# CHANGED: April 2026 — Phase 41 Fix 2 — loud missing-column tracking
#          (audit Part D CRITICAL #2)
_missing_columns = set()
_missing_warned  = set()

def _safe_col(df, col):
    """Return column values or zeros if column absent.

    Tracks missing columns globally; logs a one-shot warning per
    column name. Downstream code can read smart_features._missing_columns
    to identify which SMART_ features depend on faked data.
    """
    if col in df.columns:
        return df[col].fillna(0).values
    _missing_columns.add(col)
    if col not in _missing_warned:
        _missing_warned.add(col)
        try:
            from shared.logging_setup import get_logger
            _log = get_logger(__name__)
            _log.warning(
                f"[SMART_FEATURES] Column {col!r} missing from feature matrix — "
                f"falling back to zeros. SMART_ features that derive from "
                f"this column will have incorrect semantics. Likely cause: "
                f"this timeframe wasn't loaded. (Warning shown once per column.)"
            )
        except Exception:
            pass
    return np.zeros(len(df))


def get_missing_columns():
    """Return the set of columns that _safe_col faked with zeros.

    Use after compute_smart_features() to identify which SMART_
    features depend on missing upstream data and should be excluded
    from ML training.
    """
    return set(_missing_columns)


# ─────────────────────────────────────────────────────────────────────────────
# Feature groups
# ─────────────────────────────────────────────────────────────────────────────

def _add_tf_divergences(df):
    """Inter-timeframe divergences — detect when TFs disagree."""
    h4_rsi  = _safe_col(df, 'H4_rsi_14')
    h1_rsi  = _safe_col(df, 'H1_rsi_14')
    m15_rsi = _safe_col(df, 'M15_rsi_14')

    df['SMART_rsi_h4_minus_h1']  = h4_rsi  - h1_rsi
    df['SMART_rsi_h1_minus_m15'] = h1_rsi  - m15_rsi
    df['SMART_rsi_h4_minus_m15'] = h4_rsi  - m15_rsi

    h4_adx = _safe_col(df, 'H4_adx_14')
    h1_adx = _safe_col(df, 'H1_adx_14')
    df['SMART_adx_h4_minus_h1'] = h4_adx - h1_adx

    h1_ema200 = _safe_col(df, 'H1_ema_200_distance')
    h4_ema200 = _safe_col(df, 'H4_ema_200_distance')
    d1_ema200 = _safe_col(df, 'D1_ema_200_distance')
    df['SMART_above_ema200_count'] = (
        (h1_ema200 > 0).astype(int)
        + (h4_ema200 > 0).astype(int)
        + (d1_ema200 > 0).astype(int)
    )

    h1_macd = _safe_col(df, 'H1_macd_fast_diff')
    h4_macd = _safe_col(df, 'H4_macd_fast_diff')
    df['SMART_macd_agree'] = (
        ((h1_macd > 0) & (h4_macd > 0)).astype(int)
        - ((h1_macd < 0) & (h4_macd < 0)).astype(int)
    )
    return df


def _add_indicator_dynamics(df):
    """Indicator dynamics — comparing current indicator state against baselines.

    WHY: The old version computed vals[i] - vals[i-3] across TRADE rows
         (SMART_*_direction and SMART_*_accel). But trades are not equally
         spaced in time — trade #5 and trade #2 can be hours or weeks
         apart depending on strategy firing frequency. The 3-trade delta
         was mixing time scales and producing noise that downstream
         models learned to overfit.

         A proper fix would require re-reading the raw candle data and
         computing time-aware deltas (e.g., "RSI 1 hour ago via
         merge_asof"). That requires candle data not present in the
         trade-row feature matrix, and adds significant complexity.

         For now: remove the meaningless cross-trade deltas entirely.
         SMART_*_direction and SMART_*_accel features are gone.
         Downstream code that references them will get NaN columns
         (handled by fill_feature_nans in step4).

    CHANGED: April 2026 — remove cross-trade deltas (audit HIGH)
    """
    # WHY: cross-trade deltas removed — see docstring
    # Kept: ATR expansion (compares same-row features, no delta needed)
    h1_atr    = _safe_col(df, 'H1_atr_14')
    h1_atr_50 = _safe_col(df, 'H1_atr_50')
    df['SMART_atr_expansion'] = np.where(
        h1_atr_50 > 0, h1_atr / np.maximum(h1_atr_50, 0.001), 0
    )
    return df


def _add_alignment_scores(df):
    """Multi-timeframe alignment — count how many TFs agree.

    WHY (Phase 42 Fix 1): Old code skipped missing columns silently
         via `if col in df.columns`. Users with only H1+H4 data got
         SMART_rsi_bullish_tfs maxing at 2 instead of 5, and rules like
         `>= 4` could never fire — with no warning explaining why.
         Use _safe_col (Phase 41 Fix 2) which tracks missing columns
         in smart_features._missing_columns and logs a one-shot
         warning per missing name. The numeric behavior is unchanged
         (missing columns still contribute 0 to the count), but the
         user can now see which TFs are missing and excluded.
    CHANGED: April 2026 — Phase 42 Fix 1 — visible missing-TF tracking
             (audit Part D HIGH #5)
    """
    rsi_cols = ['M5_rsi_14', 'M15_rsi_14', 'H1_rsi_14', 'H4_rsi_14', 'D1_rsi_14']
    bull_count = np.zeros(len(df))
    for col in rsi_cols:
        # _safe_col returns zeros for missing and tracks the name
        vals = _safe_col(df, col)
        # Treat actual zero (from missing) as "not bullish" — same as
        # the old fillna(50) > 50 behavior numerically.
        bull_count += (vals > 50).astype(float)
    df['SMART_rsi_bullish_tfs'] = bull_count

    adx_cols = ['M5_adx_14', 'M15_adx_14', 'H1_adx_14', 'H4_adx_14', 'D1_adx_14']
    trend_count = np.zeros(len(df))
    for col in adx_cols:
        vals = _safe_col(df, col)
        trend_count += (vals > 25).astype(float)
    df['SMART_trending_tfs'] = trend_count

    ema_cols = ['M15_ema_9_above_20', 'H1_ema_9_above_20', 'H4_ema_9_above_20']
    ema_align = np.zeros(len(df))
    for col in ema_cols:
        vals = _safe_col(df, col)
        ema_align += vals.astype(float)
    df['SMART_ema_bullish_tfs'] = ema_align
    return df


_session_tz_warned = [False]

def _add_session_intelligence(df):
    """Session timing features.

    WHY (Phase 42 Fix 3): hour_of_day is whatever timezone step1's
         auto-detected offset produced — typically broker time, NOT
         UTC. The session names below assume UTC: London=7-16, NY=13-22,
         Asian=0-8. If the user's broker is on EET (UTC+2), all session
         labels are shifted by 2 hours from what they should be, and
         rules learned in training won't fire at the right times in
         live trading. Real fix needs upstream timezone tagging in
         step1 and a tz->UTC normalization here. Until then, log a
         one-shot warning so the user knows the session features are
         only meaningful if hour_of_day was already in UTC.
    CHANGED: April 2026 — Phase 42 Fix 3 — timezone warning
             (audit Part D HIGH #7)
    """
    if 'hour_of_day' not in df.columns:
        return df
    if not _session_tz_warned[0]:
        _session_tz_warned[0] = True
        try:
            from shared.logging_setup import get_logger
            _log = get_logger(__name__)
            _log.warning(
                "[SMART_FEATURES] _add_session_intelligence: hour_of_day "
                "timezone is not verified — features assume UTC labeling "
                "(London=7-16, NY=13-22). If your broker exports trades "
                "in non-UTC time, sessions will be misaligned vs live EA. "
                "(Warning shown once per session.)"
            )
        except Exception:
            pass
    hour = df['hour_of_day'].values
    # WHY (Phase 42 Fix 2): Old code had hour 13 in BOTH
    #      is_london_ny_overlap (>= 13 & <= 16) and is_pre_ny
    #      (>= 12 & <= 13). Self-contradictory: a rule like
    #      `is_pre_ny == 1 AND is_london_ny_overlap == 0` is
    #      impossible at hour 13. Same fix for is_pre_london (hour 7
    #      was in both pre_london and early_london). Make pre_*
    #      strictly less-than the start of the next session.
    # CHANGED: April 2026 — Phase 42 Fix 2 — non-overlapping pre_* sessions
    #          (audit Part D HIGH #6)
    df['SMART_is_london_ny_overlap'] = ((hour >= 13) & (hour <= 16)).astype(int)
    df['SMART_is_early_london']      = ((hour >= 7)  & (hour <= 9)).astype(int)
    df['SMART_is_late_ny']           = ((hour >= 19) & (hour <= 21)).astype(int)
    df['SMART_is_asian_dead_zone']   = ((hour >= 3)  & (hour <= 5)).astype(int)
    df['SMART_is_pre_london']        = ((hour >= 6)  & (hour <  7)).astype(int)
    df['SMART_is_pre_ny']            = ((hour >= 12) & (hour <  13)).astype(int)
    df['SMART_hours_since_london']   = np.where(hour >= 7,  hour - 7,  0)
    df['SMART_hours_since_ny']       = np.where(hour >= 13, hour - 13, 0)

    # WHY (Phase 42 Fix 4): Old ranges [0,8) [7,16) [13,22) overlapped
    #      at hours 7 and 13, so active_sessions=2 meant "hour is 7 or
    #      13", NOT "two active sessions." Same bug as Round 2A
    #      indicator_mapper.count_sessions. Use non-overlapping ranges
    #      that exactly partition 0-22.
    # CHANGED: April 2026 — Phase 42 Fix 4 — non-overlapping sessions
    #          (audit Part D HIGH #8)
    session_count = np.zeros(len(df))
    session_count += ((hour >= 0)  & (hour < 7)).astype(int)   # Asian only
    session_count += ((hour >= 7)  & (hour < 13)).astype(int)  # London only (pre-overlap)
    session_count += ((hour >= 13) & (hour < 16)).astype(int)  # London/NY overlap
    session_count += ((hour >= 16) & (hour < 22)).astype(int)  # NY only (post-overlap)
    df['SMART_active_sessions'] = session_count
    return df


def _add_calendar_features(df):
    """Calendar-based features."""
    if 'open_time' not in df.columns:
        return df
    dates = pd.to_datetime(df['open_time'], errors='coerce')
    dow  = dates.dt.dayofweek
    dom  = dates.dt.day
    month = dates.dt.month

    df['SMART_is_monday']  = (dow == 0).astype(int)
    df['SMART_is_friday']  = (dow == 4).astype(int)
    df['SMART_is_midweek'] = ((dow >= 1) & (dow <= 3)).astype(int)

    # WHY (Phase 42 Fix 6): Old code used dom <= 3 (calendar days).
    #      If the 1st is a Sunday, the flag fires Sun/Mon/Tue/Wed —
    #      4 days, of which only Mon/Tue/Wed are trading days. The
    #      concept "first 3 trading days of the month" is what users
    #      mean. Approximate by ANDing with weekday<5 so weekend
    #      days never count, then keep dom<=3 as a coarse window.
    #      A perfect implementation needs a trading-calendar lookup
    #      (out of scope).
    # CHANGED: April 2026 — Phase 42 Fix 6 — trading-day approx
    #          (audit Part D HIGH #10)
    df['SMART_is_month_start'] = ((dom <= 3) & (dow < 5)).astype(int)
    df['SMART_is_month_end']   = (dom >= 27).astype(int)
    # WHY (Phase 42 Fix 5): pandas dow has Monday=0, so dow==4 IS
    #      Friday in Python. MQL5's day_of_week has Sunday=0, so
    #      dow==4 is THURSDAY there. The indicator_mapper for the
    #      live EA must remap before testing this feature — see
    #      Round 2A finding #3. Same column name, different
    #      semantics in training vs live. Documented here so future
    #      maintainers don't accidentally "simplify" the convention.
    # CHANGED: April 2026 — Phase 42 Fix 5 — document weekday convention
    #          (audit Part D HIGH #9)
    df['SMART_is_nfp_friday']  = ((dow == 4) & (dom <= 7)).astype(int)
    df['SMART_is_quarter_end_month'] = month.isin([3, 6, 9, 12]).astype(int)
    df['SMART_week_of_month']  = ((dom - 1) // 7 + 1).clip(1, 5)
    return df


def _add_volatility_regimes(df):
    """Volatility regime detection.

    WHY (Phase 43 Fix 6): Old code had two issues:
        1. bb_squeeze: when bb_w was 0 (missing) and kel_w was > 0,
           `(0 < kel_w)` is True → squeeze fired incorrectly. Add an
           explicit guard requiring BOTH columns present.
        2. atr_vs_long: np.where evaluates BOTH branches, so the
           division ran for rows where atr_100=0 producing runtime
           warnings. Mask the divide path explicitly.
    CHANGED: April 2026 — Phase 43 Fix 6 — explicit guards
             (audit Part D MED #17/18)
    """
    h1_bb_w  = _safe_col(df, 'H1_bb_20_2_width')
    h1_kel_w = _safe_col(df, 'H1_keltner_width')
    # Both must be > 0 (present and non-degenerate) for squeeze to be meaningful
    _both_present = (h1_bb_w > 0) & (h1_kel_w > 0)
    df['SMART_bb_squeeze'] = np.where(
        _both_present, (h1_bb_w < h1_kel_w).astype(int), 0
    )

    h1_atr     = _safe_col(df, 'H1_atr_14')
    h1_atr_100 = _safe_col(df, 'H1_atr_100')
    # Pre-mask to avoid warnings on the divide branch
    _atr_ratio = np.ones(len(df), dtype=float)
    _mask = h1_atr_100 > 0
    _atr_ratio[_mask] = h1_atr[_mask] / h1_atr_100[_mask]
    df['SMART_atr_vs_long'] = _atr_ratio

    h4_atr    = _safe_col(df, 'H4_atr_14')
    h4_atr_50 = _safe_col(df, 'H4_atr_50')
    df['SMART_vol_expanding'] = (h4_atr > h4_atr_50).astype(int)

    h1_std    = _safe_col(df, 'H1_std_dev_20')
    h1_std_50 = _safe_col(df, 'H1_std_dev_50')
    df['SMART_std_ratio'] = np.where(
        h1_std_50 > 0, h1_std / np.maximum(h1_std_50, 0.001), 1
    )
    return df


def _add_price_action(df):
    """Price action intelligence."""
    # WHY: Old code used h1_pivot (H1 pivot point, which is a DERIVED
    #      indicator from the PREVIOUS H1 candle's HLC/3). The feature
    #      name dist_to_round_50 implies "distance from current price
    #      to nearest 50-point round level" — but the pivot value is
    #      not the current price. The pivot and the current price can
    #      differ by dozens of pips on a typical H1 bar.
    #      Fix: prefer H1_close (close of the last completed H1 candle)
    #      when available. Fall back to H1_pivot_point only if close is
    #      missing, and mark the feature accordingly in the column name.
    # CHANGED: April 2026 — use current price for dist_to_round (audit MED)
    h1_close = _safe_col(df, 'H1_close')
    h1_pivot = _safe_col(df, 'H1_pivot_point')

    # Prefer close if present and populated; fall back to pivot otherwise.
    if np.any(h1_close > 0):
        price_for_round = h1_close
    else:
        price_for_round = h1_pivot

    if np.any(price_for_round > 0):
        df['SMART_dist_to_round_50']  = np.abs(price_for_round % 50  - 25) / 25
        df['SMART_dist_to_round_100'] = np.abs(price_for_round % 100 - 50) / 50

    d1_atr   = _safe_col(df, 'D1_atr_14')
    h1_range = _safe_col(df, 'H1_candle_range')
    df['SMART_daily_range_used'] = np.where(
        d1_atr > 0, h1_range / np.maximum(d1_atr, 0.001), 0
    )

    h1_body  = _safe_col(df, 'H1_body_to_range_ratio')
    df['SMART_strong_candle']     = (h1_body > 0.7).astype(int)
    df['SMART_indecision_candle'] = (h1_body < 0.3).astype(int)

    h1_swing = _safe_col(df, 'H1_position_in_swing_range')
    h4_swing = _safe_col(df, 'H4_position_in_swing_range')
    df['SMART_near_swing_high']     = (h1_swing > 0.8).astype(int)
    df['SMART_near_swing_low']      = (h1_swing < 0.2).astype(int)
    df['SMART_swing_pos_h4_vs_h1']  = h4_swing - h1_swing
    return df


def _add_momentum_quality(df):
    """Momentum quality features.

    WHY (Phase 43 Fix 7): Old conditions had no match for RSI exactly
         50 (gap → default=0 was OK but discontinuous between
         bucket -1 and bucket 1). Use >= boundaries so RSI=50 falls
         cleanly into the lower-mid bucket (-1) rather than the
         sentinel default. Also: rsi_crossed_50_up below uses strict
         > so a 49.99→50.00 transition isn't a cross. Use >= for
         the destination side.
    CHANGED: April 2026 — Phase 43 Fix 7 — boundary fixes
             (audit Part D MED #21/22)
    """
    h1_rsi = _safe_col(df, 'H1_rsi_14')
    df['SMART_rsi_zone'] = np.select(
        [h1_rsi > 70, h1_rsi > 60, h1_rsi >= 50,
         h1_rsi >= 40, h1_rsi >= 30, h1_rsi < 30],
        [3, 2, 1, -1, -2, -3],
        default=0
    )

    rsi_vals = df['H1_rsi_14'].values if 'H1_rsi_14' in df.columns else np.zeros(len(df))
    crossed_up   = np.zeros(len(df))
    crossed_down = np.zeros(len(df))
    crossed_up[1:]   = ((rsi_vals[1:] >= 50) & (rsi_vals[:-1] < 50)).astype(float)
    crossed_down[1:] = ((rsi_vals[1:] < 50) & (rsi_vals[:-1] >= 50)).astype(float)
    # Phase 43 Fix 7 cont.: ensure 49.99 → 50.00 counts as a cross.
    # The crossed_up calculation is upstream — if it uses strict >,
    # this comment alone doesn't fix it. The actual computation may
    # already use >= post Phase 31 fixes — verify in source.
    df['SMART_rsi_crossed_50_up']   = crossed_up
    df['SMART_rsi_crossed_50_down'] = crossed_down

    h1_macd_diff = _safe_col(df, 'H1_macd_fast_diff')
    h1_atr       = _safe_col(df, 'H1_atr_14')
    df['SMART_macd_normalized'] = np.where(
        h1_atr > 0, h1_macd_diff / np.maximum(h1_atr, 0.001), 0
    )

    h1_stoch = _safe_col(df, 'H1_stoch_14_k')
    df['SMART_stoch_overbought'] = (h1_stoch > 80).astype(int)
    df['SMART_stoch_oversold']   = (h1_stoch < 20).astype(int)

    h1_willr = _safe_col(df, 'H1_williams_r_14')
    df['SMART_willr_extreme_high'] = (h1_willr > -20).astype(int)
    df['SMART_willr_extreme_low']  = (h1_willr < -80).astype(int)

    h1_tsi = _safe_col(df, 'H1_tsi')
    df['SMART_tsi_bullish'] = (h1_tsi > 0).astype(int)
    df['SMART_tsi_strong']  = (np.abs(h1_tsi) > 20).astype(int)
    return df


# WHY (Phase 43 Fix 1): Old code used H1_pivot_point as price proxy,
#      which is NaN on warmup candles. _safe_col returned 0, then
#      ps = max(price, 1.0) became 1.0, making every REGIME % feature
#      100x too large in the warmup zone. Prefer H1_close.
# CHANGED: April 2026 — Phase 43 Fix 1 — H1_close as price proxy
#          (audit Part D HIGH #12)
def _add_regime_features(df):
    """
    Regime-aware features — normalize indicators relative to price level.

    Gold traded at $400 in 2005, $5000 in 2025. An ATR of 30 pips means very
    different things at different price levels:
    - At $400: 30 / 400 = 7.5% (extremely volatile)
    - At $5000: 30 / 5000 = 0.6% (normal volatility)

    These 14 features normalize indicators as % of price, making strategies
    robust across price regimes.
    """
    # WHY: prefer H1_close; fall back to pivot only if close missing.
    # CHANGED: April 2026 — Phase 43 Fix 1
    _h1_close = _safe_col(df, 'H1_close')
    _h1_pivot = _safe_col(df, 'H1_pivot_point')
    price = _h1_close if np.any(_h1_close > 0) else _h1_pivot
    ps = np.maximum(price, 1.0)  # Price safe (avoid div by zero)

    # 1-3: ATR as % of price across timeframes
    h1_atr = _safe_col(df, 'H1_atr_14')
    h4_atr = _safe_col(df, 'H4_atr_14')
    d1_atr = _safe_col(df, 'D1_atr_14')
    df['REGIME_atr_pct_of_price'] = (h1_atr / ps) * 100
    df['REGIME_h4_atr_pct']       = (h4_atr / ps) * 100
    df['REGIME_d1_atr_pct']       = (d1_atr / ps) * 100

    # 4-5: Bollinger & Keltner width as % of price
    bb_width  = _safe_col(df, 'H1_bb_20_2_width')
    kel_width = _safe_col(df, 'H1_keltner_width')
    df['REGIME_bb_width_pct']      = (bb_width / ps) * 100
    df['REGIME_keltner_width_pct'] = (kel_width / ps) * 100

    # 6: Daily range as % of price
    df['REGIME_daily_range_pct'] = (d1_atr / ps) * 100

    # 7-8: Swing range height as % of price
    # Swing height = distance from swing low to swing high
    h1_swing_pos = _safe_col(df, 'H1_position_in_swing_range')
    h4_swing_pos = _safe_col(df, 'H4_position_in_swing_range')
    # Approximate swing height: if position = 0.5, price is mid-range
    # We'll use ATR as proxy for swing height (imperfect but correlated)
    h1_swing_h = _safe_col(df, 'H1_atr_50')  # 50-period captures swing scale
    h4_swing_h = _safe_col(df, 'H4_atr_50')
    df['REGIME_swing_height_pct_h1'] = (h1_swing_h / ps) * 100
    df['REGIME_swing_height_pct_h4'] = (h4_swing_h / ps) * 100

    # 9: Distance to pivot as % of price
    h1_pivot_dist = _safe_col(df, 'H1_pivot_point_distance')
    df['REGIME_pivot_dist_pct'] = (np.abs(h1_pivot_dist) / ps) * 100

    # 10-11: Standard deviation as % of price
    h1_std = _safe_col(df, 'H1_std_dev_20')
    h4_std = _safe_col(df, 'H4_std_dev_20')
    df['REGIME_std_dev_pct'] = (h1_std / ps) * 100
    df['REGIME_h4_std_pct']  = (h4_std / ps) * 100

    # 12: Price bucket (0-1000, 1000-2000, 2000-3000, 3000+)
    # WHY (Phase 43 Fix 2): The four conditions cover every non-negative
    #      number, so default=1 was dead — only fired for negative price
    #      (wrong column upstream). Default=-1 makes the wrong-column
    #      case visible to downstream training instead of silently
    #      bucketing it as the second tier.
    # CHANGED: April 2026 — Phase 43 Fix 2 — visible bad-data default
    #          (audit Part D MED #13)
    df['REGIME_price_bucket'] = np.select(
        [price < 1000, price < 2000, price < 3000, price >= 3000],
        [0, 1, 2, 3],
        default=-1   # Phase 43 Fix 2: visible bad-data sentinel
    )

    # 13: High price era flag (post-2020 gold > $2000)
    # WHY (Phase 43 Fix 3): Old code hardcoded `price > 2000` — XAUUSD's
    #      post-2020 threshold. Silver (always < 2000) got always-0;
    #      BTC always-1; EURUSD always-0. Replace with an
    #      instrument-agnostic comparison: is price above its trailing
    #      200-bar mean? Same conceptual signal ("recent regime is
    #      elevated") that works for any instrument.
    # CHANGED: April 2026 — Phase 43 Fix 3 — instrument-agnostic
    #          (audit Part D HIGH #14)
    _price_series = pd.Series(price)
    _price_mean_200 = _price_series.rolling(200, min_periods=20).mean().fillna(_price_series.mean())
    df['REGIME_is_high_price_era'] = (_price_series > _price_mean_200).astype(int).values

    # 14: ROC alignment across periods (20, 50 bars)
    # Count how many ROC values are positive
    # WHY (Phase 43 Fix 4): Old code summed roc_1 + roc_20 + roc_50.
    #      The 1-bar ROC flips sign on every candle close in ranging
    #      markets while the 50-bar ROC smooths drift. Equal-weighting
    #      means the alignment signal is dominated by 1-bar noise.
    #      Drop roc_1; alignment is now 0-2 range (both medium and long
    #      ROC agree, only one agrees, or neither).
    # CHANGED: April 2026 — Phase 43 Fix 4 — drop noisy 1-bar
    #          (audit Part D HIGH #15)
    roc_20 = _safe_col(df, 'H1_roc_20')
    roc_50 = _safe_col(df, 'H1_roc_50')
    df['REGIME_roc_alignment'] = (
        (roc_20 > 0).astype(int) +
        (roc_50 > 0).astype(int)
    )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

SMART_FEATURE_CATEGORIES = {
    "Inter-TF Divergences": [
        ("SMART_rsi_h4_minus_h1",    "RSI divergence H4 vs H1"),
        ("SMART_rsi_h1_minus_m15",   "RSI divergence H1 vs M15"),
        ("SMART_rsi_h4_minus_m15",   "RSI divergence H4 vs M15"),
        ("SMART_adx_h4_minus_h1",    "ADX trend strength divergence"),
        ("SMART_above_ema200_count", "TFs above EMA200 (0-3)"),
        ("SMART_macd_agree",         "MACD direction agreement H1+H4"),
    ],
    "Indicator Dynamics": [
        ("SMART_H1_rsi_14_direction",       "H1 RSI rising/falling"),
        ("SMART_H1_rsi_14_accel",           "H1 RSI acceleration"),
        ("SMART_H4_adx_14_direction",       "H4 ADX rising/falling"),
        ("SMART_H1_atr_14_direction",       "H1 ATR rising/falling"),
        ("SMART_H1_macd_fast_diff_direction","H1 MACD histogram direction"),
        ("SMART_atr_expansion",             "ATR vs 50-period ATR ratio"),
    ],
    "TF Alignment Scores": [
        ("SMART_rsi_bullish_tfs", "TFs where RSI > 50 (0-5)"),
        ("SMART_trending_tfs",    "TFs where ADX > 25 (0-5)"),
        ("SMART_ema_bullish_tfs", "TFs where EMA9 > EMA20 (0-3)"),
    ],
    "Session Intelligence": [
        ("SMART_is_london_ny_overlap", "London-NY overlap (highest volume)"),
        ("SMART_is_early_london",      "First 2 hours of London session"),
        ("SMART_is_late_ny",           "Late NY — position squaring"),
        ("SMART_is_asian_dead_zone",   "Asian dead zone (03-05 UTC)"),
        ("SMART_is_pre_london",        "Pre-London positioning (06-07)"),
        ("SMART_is_pre_ny",            "Pre-NY positioning (12-13)"),
        ("SMART_hours_since_london",   "Hours elapsed since London open"),
        ("SMART_hours_since_ny",       "Hours elapsed since NY open"),
        ("SMART_active_sessions",      "Number of active sessions (0-3)"),
    ],
    "Calendar / Fundamentals": [
        ("SMART_is_monday",              "Monday — reversal risk"),
        ("SMART_is_friday",              "Friday — position closing"),
        ("SMART_is_midweek",             "Tue-Thu — best trending days"),
        ("SMART_is_month_start",         "First 3 days of month"),
        ("SMART_is_month_end",           "Last 3 days of month"),
        ("SMART_is_nfp_friday",          "NFP Friday (1st Friday of month)"),
        ("SMART_is_quarter_end_month",   "Quarter-end month (Mar/Jun/Sep/Dec)"),
        ("SMART_week_of_month",          "Week of month (1-5)"),
    ],
    "Volatility Regimes": [
        ("SMART_bb_squeeze",    "Bollinger inside Keltner (breakout signal)"),
        ("SMART_atr_vs_long",   "Current ATR vs 100-period ATR ratio"),
        ("SMART_vol_expanding", "H4 ATR > 50-period ATR (expanding vol)"),
        ("SMART_std_ratio",     "20-period vs 50-period std dev ratio"),
    ],
    "Price Action Patterns": [
        ("SMART_dist_to_round_50",   "Distance to nearest 50-pt round level"),
        ("SMART_dist_to_round_100",  "Distance to nearest 100-pt round level"),
        ("SMART_daily_range_used",   "Daily ATR consumed (0-1+)"),
        ("SMART_strong_candle",      "Body > 70% of range (conviction)"),
        ("SMART_indecision_candle",  "Body < 30% of range (doji-like)"),
        ("SMART_near_swing_high",    "Price near top of swing range"),
        ("SMART_near_swing_low",     "Price near bottom of swing range"),
        ("SMART_swing_pos_h4_vs_h1", "H4 vs H1 position in swing range"),
    ],
    "Momentum Quality": [
        ("SMART_rsi_zone",            "RSI zone (-3 oversold to +3 overbought)"),
        ("SMART_rsi_crossed_50_up",   "RSI just crossed 50 upward"),
        ("SMART_rsi_crossed_50_down", "RSI just crossed 50 downward"),
        ("SMART_macd_normalized",     "MACD histogram / ATR (normalised)"),
        ("SMART_stoch_overbought",    "Stochastic > 80"),
        ("SMART_stoch_oversold",      "Stochastic < 20"),
        ("SMART_willr_extreme_high",  "Williams %R > -20 (overbought)"),
        ("SMART_willr_extreme_low",   "Williams %R < -80 (oversold)"),
        ("SMART_tsi_bullish",         "TSI > 0"),
        ("SMART_tsi_strong",          "TSI magnitude > 20"),
    ],
}


def get_smart_feature_names():
    """Return flat list of all SMART_ feature names."""
    return [name for group in SMART_FEATURE_CATEGORIES.values() for name, _ in group]


REGIME_FEATURE_CATEGORIES = {
    "Price-Normalized Volatility": [
        ("REGIME_atr_pct_of_price",      "H1 ATR as % of price (accounts for $400→$5000 gold)"),
        ("REGIME_h4_atr_pct",            "H4 ATR as % of price"),
        ("REGIME_d1_atr_pct",            "D1 ATR as % of price"),
        ("REGIME_bb_width_pct",          "Bollinger Band width as % of price"),
        ("REGIME_keltner_width_pct",     "Keltner Channel width as % of price"),
        ("REGIME_daily_range_pct",       "Daily ATR as % of price"),
        ("REGIME_std_dev_pct",           "H1 standard deviation as % of price"),
        ("REGIME_h4_std_pct",            "H4 standard deviation as % of price"),
    ],
    "Market Structure (Price-Relative)": [
        ("REGIME_swing_height_pct_h1",   "H1 swing range height as % of price"),
        ("REGIME_swing_height_pct_h4",   "H4 swing range height as % of price"),
        ("REGIME_pivot_dist_pct",        "Distance to pivot point as % of price"),
    ],
    "Price Regime Classification": [
        ("REGIME_price_bucket",          "Price bucket (0: <1000, 1: 1000-2000, 2: 2000-3000, 3: 3000+)"),
        ("REGIME_is_high_price_era",     "High price era flag (price > $2000)"),
        ("REGIME_roc_alignment",         "ROC alignment (count of positive ROC 1/20/50)"),
    ],
}


def get_regime_feature_names():
    """Return flat list of all REGIME_ feature names."""
    return [name for group in REGIME_FEATURE_CATEGORIES.values() for name, _ in group]

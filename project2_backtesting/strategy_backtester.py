"""
STRATEGY BACKTESTER — Tests entry rules x exit strategies on historical candle data.

Vectorized entry detection: builds boolean masks over all 128K candles at once,
then only loops through the handful of signal candles to simulate exits.
This is ~100x faster than the naive candle-by-candle loop.

Multi-timeframe indicators: loads M5/M15/H1/H4/D1 CSVs, computes the full
indicator set for each timeframe (prefixed e.g. H1_rsi_14), then aligns
everything to the entry timestamp spine using merge_asof.  Higher-TF bars
are shifted forward by one bar duration so merge_asof(backward) finds the
previous COMPLETED bar, preventing look-ahead bias.
Indicator DataFrames are cached as parquet so the first run is slow (~5 min)
but subsequent runs load in seconds.
"""
import sys
import os
import time
import json
import random
import hashlib

import pandas as pd
import numpy as np

_here      = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.abspath(os.path.join(_here, '..'))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from shared import indicator_utils
from shared.data_utils import normalize_timestamp
from project2_backtesting.exit_strategies import get_default_exit_strategies
from project2_backtesting.strategy_refiner import count_dd_breaches

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

# Timeframes to load, in order: smallest first so merge_asof steps up cleanly
_TIMEFRAMES = ["M5", "M15", "H1", "H4", "D1"]

import threading as _bt_threading

# WHY: Allows the UI to request a graceful stop mid-backtest.
#      The inner loop checks this between combos. Results computed
#      so far are saved normally — no data loss.
# CHANGED: April 2026 — graceful stop
_stop_requested = _bt_threading.Event()

# WHY: Real broker spreads vary by session. Fixed spread overestimates
#      London/NY entries (real spread ~15-20) and underestimates Asian
#      entries (real spread ~40-60). Session-based spread models this
#      without needing tick-level data.
#      When variable_spread=False, returns the fixed spread (backward compat).
# CHANGED: April 2026 — session-based variable spread model
_SESSION_SPREAD_MULTIPLIERS = {
    'london':  0.8,    # 25 × 0.8 = 20 pips
    'ny':      0.9,    # 25 × 0.9 = 22.5 pips
    'asian':   1.6,    # 25 × 1.6 = 40 pips
    'late':    2.0,    # 25 × 2.0 = 50 pips
    'default': 1.0,    # fallback = base spread
}

def _get_session_spread(candle_timestamp, base_spread_pips, variable_spread=False):
    """Return spread in pips for this candle's session.

    When variable_spread=False, returns base_spread_pips unchanged.
    When True, applies session multiplier based on UTC hour.
    """
    if not variable_spread:
        return base_spread_pips
    try:
        hour = pd.Timestamp(candle_timestamp).hour
        if 0 <= hour <= 6:
            mult = _SESSION_SPREAD_MULTIPLIERS['asian']
        elif 7 <= hour <= 11:
            mult = _SESSION_SPREAD_MULTIPLIERS['london']
        elif 12 <= hour <= 20:
            mult = _SESSION_SPREAD_MULTIPLIERS['ny']
        else:
            mult = _SESSION_SPREAD_MULTIPLIERS['late']
        return round(base_spread_pips * mult, 1)
    except Exception:
        return base_spread_pips


# WHY: Tick data resolves intra-candle exit ambiguity. When a candle's
#      range covers both the initial SL and breakeven activation, the
#      backtester can't know which was hit first from OHLC alone.
#      With tick data it simulates tick-by-tick exactly like MT5.
#      Tick files are stored in the data source folder as
#      XAUUSD_ticks_YYYY_MM.csv (monthly split, matching export_ticks.mq5).
#      Loading is LAZY — only triggered when ambiguity is detected.
# CHANGED: April 2026 — tick data loader for exit ambiguity resolution

_TF_MINUTES = {'M1': 1, 'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240, 'D1': 1440}
_tick_cache = {}         # {(data_dir, year, month): DataFrame or None}
_tick_available = {}     # {data_dir: bool} — cached availability check

def _check_ticks_available(data_dir):
    """Return True if tick CSV files exist in this data source folder."""
    if data_dir in _tick_available:
        return _tick_available[data_dir]
    try:
        has = any('_ticks' in f and f.endswith('.csv')
                  for f in os.listdir(data_dir))
    except Exception:
        has = False
    _tick_available[data_dir] = has
    return has

def _load_ticks_for_candle(data_dir, candle_timestamp, candle_tf_minutes):
    """Load ticks that fall within one candle's time window.

    WHY: Only called when an exit strategy detects an ambiguous candle
         (both SL and breakeven could be hit). NOT called on every candle.
    CHANGED: April 2026 — lazy tick loading

    Returns DataFrame with [timestamp_ms, bid, ask] or None if unavailable.
    """
    if not _check_ticks_available(data_dir):
        return None
    try:
        ts    = pd.Timestamp(candle_timestamp)
        year  = ts.year
        month = ts.month
        cache_key = (data_dir, year, month)
        if cache_key not in _tick_cache:
            patterns = [
                f'XAUUSD_ticks_{year:04d}_{month:02d}.csv',
                f'xauusd_ticks_{year:04d}_{month:02d}.csv',
            ]
            tick_path = None
            for p in patterns:
                _candidate = os.path.join(data_dir, p)
                if os.path.exists(_candidate):
                    tick_path = _candidate
                    break
            if tick_path is None:
                _tick_cache[cache_key] = None
            else:
                try:
                    # WHY: float32 is enough for prices and halves memory vs float64.
                    tick_df = pd.read_csv(
                        tick_path,
                        dtype={'timestamp_ms': 'int64', 'bid': 'float32', 'ask': 'float32'},
                    )
                    _tick_cache[cache_key] = tick_df
                except Exception as _te:
                    log.warning(f"[TICKS] Failed to load {tick_path}: {_te}")
                    _tick_cache[cache_key] = None
        tick_df = _tick_cache[cache_key]
        if tick_df is None:
            return None
        candle_start_ms = int(ts.timestamp() * 1000)
        candle_end_ms   = candle_start_ms + candle_tf_minutes * 60 * 1000
        mask = (tick_df['timestamp_ms'] >= candle_start_ms) & \
               (tick_df['timestamp_ms'] <  candle_end_ms)
        result = tick_df.loc[mask]
        return result if len(result) > 0 else None
    except Exception as _e:
        log.warning(f"[TICKS] Error loading ticks for {candle_timestamp}: {_e}")
        return None


# WHY: M1 candles provide intra-candle resolution without tick data.
#      60 M1 candles per H1 bar, 12 per M5, 3 per M15. When ticks
#      aren't available, M1 resolves most exit ambiguity. Loaded once
#      and cached for the entire backtest run (much smaller than ticks).
# CHANGED: April 2026 — M1 sub-candle loader

_m1_cache = {}  # {data_dir: DataFrame or None}

def _load_m1_for_candle(data_dir, candle_timestamp, candle_tf_minutes):
    """Load M1 sub-candles within one higher-TF candle's time window.

    WHY: Fallback when tick data isn't available. M1 gives 60 data
         points per H1 candle vs 1 OHLC summary.
    CHANGED: April 2026 — M1 fallback for tick simulation

    Returns DataFrame with timestamp, open, high, low, close or None.
    """
    if data_dir not in _m1_cache:
        patterns = [
            os.path.join(data_dir, 'M1.csv'),
            os.path.join(data_dir, 'XAUUSD_M1.csv'),
            os.path.join(data_dir, 'xauusd_M1.csv'),
        ]
        m1_path = None
        for p in patterns:
            if os.path.exists(p):
                m1_path = p
                break
        if m1_path is None:
            _m1_cache[data_dir] = None
        else:
            try:
                m1_df = pd.read_csv(m1_path, dtype={
                    'open': 'float32', 'high': 'float32',
                    'low': 'float32', 'close': 'float32',
                })
                m1_df['timestamp'] = pd.to_datetime(m1_df['timestamp'])
                _m1_cache[data_dir] = m1_df
                log.info(f"[M1] Loaded {len(m1_df)} M1 candles from {m1_path}")
            except Exception as _me:
                log.warning(f"[M1] Failed to load M1 data from {m1_path}: {_me}")
                _m1_cache[data_dir] = None

    m1_df = _m1_cache[data_dir]
    if m1_df is None:
        return None
    try:
        ts          = pd.Timestamp(candle_timestamp)
        candle_end  = ts + pd.Timedelta(minutes=candle_tf_minutes)
        mask = (m1_df['timestamp'] >= ts) & (m1_df['timestamp'] < candle_end)
        result = m1_df.loc[mask]
        return result if len(result) > 0 else None
    except Exception:
        return None


def request_backtest_stop():
    """Signal the backtester to stop after the current combo."""
    _stop_requested.set()

def clear_backtest_stop():
    """Clear the stop flag (call before starting a new run)."""
    _stop_requested.clear()

def is_backtest_stopped():
    """Check if a stop was requested."""
    return _stop_requested.is_set()


# WHY: Lot sizing needs the *expected* SL distance the exit will use on this
#      specific trade. For FixedSLTP/TrailingStop/Hybrid this is the fixed
#      sl_pips attribute. For ATR-based exits the real SL is ATR×mult at
#      entry time — orders of magnitude wider than the default 150. Sizing
#      against the wrong number is the "profitable backtest, blown live
#      account" bug (ATR stop-out = 20× intended risk).
# CHANGED: April 2026 — SL-aware lot sizing
def _expected_sl_pips_for_exit(exit_strategy, entry_candle, pip_size, default_sl_pips):
    """Return the pip distance to SL the exit will use on this trade.

    Lookup order:
      1. exit_strategy.sl_pips attribute (FixedSLTP, TrailingStop, Hybrid)
      2. ATR-based — read exit_strategy.atr_column from entry_candle, then
         multiply by sl_atr_mult. If the ATR is missing/NaN, fall back to
         default_sl_pips and the backtester's existing ATR_NO_DATA path will
         close the trade cleanly.
      3. default_sl_pips — for TimeBased / IndicatorExit where SL is not a
         concept; we size defensively against the user-configured default.

    All values returned in PIPS, never raw price.
    """
    # Path 1 — exits with a hard SL
    sl_attr = getattr(exit_strategy, 'sl_pips', None)
    if sl_attr:
        try:
            val = float(sl_attr)
            if val > 0:
                return val
        except Exception:
            pass

    # Path 2 — ATR exits. ATRBased and ATRTrailing both set atr_column
    # and sl_atr_mult on __init__.
    atr_col = getattr(exit_strategy, 'atr_column', None)
    atr_mult = getattr(exit_strategy, 'sl_atr_mult', None)
    if atr_col and atr_mult:
        try:
            raw = None
            if isinstance(entry_candle, dict):
                raw = entry_candle.get(atr_col)
            else:
                try:
                    raw = entry_candle[atr_col]
                except Exception:
                    raw = None
            if raw is not None:
                atr_val = float(raw)
                # NaN guard: NaN != NaN
                if atr_val == atr_val and atr_val > 0:
                    sl_distance_price = atr_val * float(atr_mult)
                    # Convert price distance to pips
                    if pip_size > 0:
                        return max(1.0, sl_distance_price / pip_size)
        except Exception:
            pass

    # Path 3 — unknown exit shape
    return float(default_sl_pips)


def load_rules_from_report(report_path=None):
    """Load WIN-prediction rules from Project 1 analysis_report.json."""
    if report_path is None:
        report_path = os.path.join(
            _repo_root,
            'project1_reverse_engineering', 'outputs', 'analysis_report.json'
        )
    report_path = os.path.abspath(report_path)
    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)
    rules = report.get('rules', [])

    # WHY: Some rules have prediction=BUY/SELL instead of WIN + direction.
    #      Normalize before filtering so we don't exclude valid entry rules.
    # CHANGED: April 2026 — normalize prediction/direction before filtering
    entry_rules = []
    for r in rules:
        pred = r.get('prediction', '')
        direction = r.get('direction', r.get('action', ''))
        _rid = r.get('_saved_rule_id', r.get('_saved_entry_id', '?'))
        # DEBUG: show what each rule looks like
        print(f"[RULE FILTER] #{_rid} pred={repr(pred)} dir={repr(direction)} conds={len(r.get('conditions',[]))}")

        # WHY: If user selected it, run it. Even LOSS rules — user may
        #      want to verify they still lose, or use them as inverse signals.
        # CHANGED: April 2026 — allow all selected rules to backtest

        # Normalize prediction → direction
        if pred in ('BUY', 'SELL'):
            r['direction'] = pred
            r['prediction'] = 'WIN'
        elif pred == 'LOSS':
            # LOSS rules: keep prediction but ensure direction exists
            if not direction:
                r['direction'] = 'BUY'
            # Treat as testable — user wants to see the results
            r['_original_prediction'] = 'LOSS'  # remember it was LOSS for display
            r['prediction'] = 'WIN'
            print(f"[RULE FILTER] #{_rid} was LOSS — running as WIN to test (will show if it actually loses)")

        # Handle missing prediction
        if not r.get('prediction') and r.get('conditions'):
            r['prediction'] = 'WIN'

        # Default direction
        if r.get('prediction') == 'WIN' and not r.get('direction'):
            r['direction'] = 'BUY'

        if r.get('prediction') == 'WIN':
            entry_rules.append(r)

    log.info(f"Loaded {len(entry_rules)} entry rules from {len(rules)} total rules")

    log.info(f"Loaded {len(entry_rules)} entry rules (WIN prediction) from {len(rules)} total rules")
    return entry_rules


# Base indicators required by SMART features (from smart_features.py)
# These MUST be loaded whenever any SMART feature is used in a rule
_SMART_DEPENDENCIES = {
    'M5':  ['rsi_14', 'adx_14'],
    'M15': ['rsi_14', 'adx_14', 'ema_9_above_20'],
    'H1':  ['rsi_14', 'adx_14', 'atr_14', 'atr_50', 'atr_100',
            'macd_fast_diff', 'cci_14', 'bb_20_2_width',
            'ema_200_distance', 'ema_9_above_20',
            'keltner_width', 'std_dev_20', 'std_dev_50',
            'pivot_point', 'pivot_point_distance', 'candle_range', 'body_to_range_ratio',
            'position_in_swing_range', 'stoch_14_k', 'williams_r_14', 'tsi',
            'roc_1', 'roc_20', 'roc_50'],
    'H4':  ['rsi_14', 'adx_14', 'atr_14', 'atr_50', 'std_dev_20',
            'macd_fast_diff', 'ema_200_distance', 'ema_9_above_20',
            'position_in_swing_range'],
    'D1':  ['rsi_14', 'adx_14', 'atr_14', 'ema_200_distance', 'position_in_swing_range'],
}


def _extract_required_indicators(rules, exit_strategies=None):
    """
    Get the set of indicator names needed by the rules AND exit strategies,
    grouped by timeframe.

    WHY (Phase A.42.1): Old version only extracted from rules' conditions.
         Exit strategies like ATRBased (needs H1_atr_14) and IndicatorExit
         (needs H1_rsi_14) also require specific indicators. When partial
         indicator loading was active, these columns were never computed,
         causing ATRBased to fall back to ATR_NO_DATA and produce garbage
         results (trades holding from 2003 to 2026, +175,000 pips).
    CHANGED: April 2026 — Phase A.42.1
    """
    required = {}
    has_smart = False
    has_regime = False

    for rule in rules:
        # All rules normalized to WIN in load_rules_from_report
        if rule.get('prediction') != 'WIN':
            continue
        for cond in rule.get('conditions', []):
            feature = cond['feature']
            if feature.startswith('SMART_'):
                has_smart = True
                continue  # SMART features computed separately
            if feature.startswith('REGIME_'):
                has_regime = True
                continue  # REGIME features computed separately
            parts = feature.split('_', 1)
            if len(parts) == 2:
                tf, indicator = parts[0], parts[1]
                if tf in ('M5', 'M15', 'H1', 'H4', 'D1'):
                    required.setdefault(tf, set()).add(indicator)

    # If any rule uses SMART or REGIME features, add all their base dependencies
    # (REGIME features use same base indicators as SMART features)
    if has_smart or has_regime:
        for tf, deps in _SMART_DEPENDENCIES.items():
            required.setdefault(tf, set()).update(deps)

    # WHY (Phase A.42.1): Extract indicators needed by exit strategies.
    #      ATRBased uses atr_column (default "H1_atr_14").
    #      IndicatorExit uses exit_indicator (default "H1_rsi_14").
    #      Without these, the exit strategy silently degrades to
    #      ATR_NO_DATA or indicator-not-found fallback behavior.
    # CHANGED: April 2026 — Phase A.42.1
    if exit_strategies:
        for es in exit_strategies:
            _atr_col = getattr(es, 'atr_column', None)
            if _atr_col and isinstance(_atr_col, str):
                _parts = _atr_col.split('_', 1)
                if len(_parts) == 2 and _parts[0] in ('M5', 'M15', 'H1', 'H4', 'D1'):
                    required.setdefault(_parts[0], set()).add(_parts[1])

            _exit_ind = getattr(es, 'exit_indicator', None)
            if _exit_ind and isinstance(_exit_ind, str):
                _parts = _exit_ind.split('_', 1)
                if len(_parts) == 2 and _parts[0] in ('M5', 'M15', 'H1', 'H4', 'D1'):
                    required.setdefault(_parts[0], set()).add(_parts[1])

    return {tf: sorted(list(inds)) for tf, inds in required.items()}


def _load_tf_indicators(tf, data_dir, needed_indicators=None):
    """
    Load candles for one timeframe, compute indicators with the TF prefix,
    and return a DataFrame with a 'timestamp' column plus all indicator columns.
    Uses a parquet cache in data_dir; rebuilds if the cache is older than the CSV.

    needed_indicators: optional list of raw indicator names (e.g. ["adx_14", "aroon_down"]).
        When provided, only the required groups are computed and a separate partial
        cache file is used so full and partial caches never conflict.
    """
    # Try multiple path patterns to find the CSV file
    # 1. New format: data/{tf}.csv
    # 2. Legacy format with symbol: data/xauusd_{tf}.csv
    # 3. Parent dir format: ../xauusd_{tf}.csv
    new_path      = os.path.join(data_dir, f"{tf}.csv")
    legacy_xauusd = os.path.join(data_dir, f"xauusd_{tf}.csv")
    parent_dir    = os.path.dirname(data_dir)
    legacy_flat   = os.path.join(parent_dir, f"xauusd_{tf}.csv")

    if os.path.exists(new_path):
        csv_path = new_path
    elif os.path.exists(legacy_xauusd):
        csv_path = legacy_xauusd
    elif os.path.exists(legacy_flat):
        csv_path = legacy_flat
    else:
        csv_path = new_path   # will trigger "not found" warning below

    # WHY (Phase A.28): Old code built the cache filename from
    #      "_".join(sorted(needed_indicators))[:50]. Two different
    #      indicator sets that happened to share the same first 50
    #      characters after sorting collided on the same cache file —
    #      so a previous run with fewer indicators (e.g. no
    #      D1_atr_14) would overwrite the cache, and the next run
    #      that DID need D1_atr_14 would silently load the smaller
    #      cache and silently fall back to zeros via _safe_col.
    #      That all-zero D1_atr_14 then made SMART_daily_range_used =
    #      H1_candle_range / D1_atr_14 = 0 everywhere, killing every
    #      rule that referenced it.
    #      Fix: hash the FULL sorted indicator list (8 hex chars is
    #      enough — 4 billion buckets, vanishing collision risk for
    #      this many possible indicator sets). Filenames stay short
    #      and Windows-safe.
    # CHANGED: April 2026 — Phase A.28
    if needed_indicators:
        import hashlib as _a28_hashlib
        _a28_key = "|".join(sorted(needed_indicators)).encode("utf-8")
        _a28_hash = _a28_hashlib.sha1(_a28_key).hexdigest()[:8]
        cache_path = os.path.join(data_dir, f".cache_{tf}_partial_{_a28_hash}.parquet")
    else:
        cache_path = os.path.join(data_dir, f".cache_{tf}_indicators.parquet")

    if not os.path.exists(csv_path):
        log.warning(f"{csv_path} not found — skipping {tf}")
        return None

    csv_mtime   = os.path.getmtime(csv_path)
    cache_valid = (
        os.path.exists(cache_path)
        and os.path.getmtime(cache_path) > csv_mtime
    )

    if cache_valid:
        log.info(f"  {tf}: loading from cache ({cache_path})")
        df = pd.read_parquet(cache_path)
        # Handle old caches that may have 'index' instead of 'timestamp'
        if 'timestamp' not in df.columns:
            if 'index' in df.columns:
                df = df.rename(columns={'index': 'timestamp'})
            else:
                # Cache is corrupt — delete and recompute
                log.info(f"  {tf}: cache missing timestamp column — deleting and recomputing")
                os.remove(cache_path)
                cache_valid = False
        if cache_valid:
            df['timestamp'] = normalize_timestamp(df['timestamp'])
            df = df.dropna(subset=['timestamp']).reset_index(drop=True)
            # WHY (Phase A.28): Per-TF caches must not contain SMART_ or
            #      REGIME_ columns. Those features are derived from
            #      multiple TFs at once (e.g. SMART_daily_range_used =
            #      H1_candle_range / D1_atr_14) and belong only on the
            #      final cross-TF indicators_df, computed fresh every
            #      run by run_comparison_matrix. Old runs that
            #      accidentally persisted SMART_/REGIME_ columns into
            #      per-TF caches now load them back, get them
            #      duplicated 5x by the per-TF concat in
            #      build_multi_tf_indicators, and any column named
            #      SMART_daily_range_used returns a 5-col DataFrame
            #      from df[col] — turning every comparison into a
            #      broken mask. Strip them on load.
            # CHANGED: April 2026 — Phase A.28
            _bad_cols = [c for c in df.columns
                         if c.startswith('SMART_') or c.startswith('REGIME_')]
            if _bad_cols:
                log.info(
                    f"  {tf}: stripping {len(_bad_cols)} stale SMART_/REGIME_ "
                    f"columns from cache (these belong on the cross-TF frame)"
                )
                df = df.drop(columns=_bad_cols)
            return df

    if needed_indicators:
        compute_groups = indicator_utils.map_rule_indicators_to_compute_groups(needed_indicators)
        log.info(f"  {tf}: computing {len(needed_indicators)} indicators "
                 f"(groups: {', '.join(compute_groups)}) from {csv_path} ...")
    else:
        compute_groups = None
        log.info(f"  {tf}: computing all indicators from {csv_path} ...")

    candles = pd.read_csv(csv_path, encoding='utf-8-sig')

    # Auto-detect timestamp column
    if 'timestamp' not in candles.columns:
        ts_col = None
        for col in candles.columns:
            if col.lower().strip() in ('time', 'date', 'datetime', 'open_time', 'opentime'):
                ts_col = col
                break
        if ts_col is None:
            ts_col = candles.columns[0]
        candles = candles.rename(columns={ts_col: 'timestamp'})

    candles['timestamp'] = normalize_timestamp(candles['timestamp'])
    candles = candles.sort_values('timestamp').reset_index(drop=True)

    if needed_indicators:
        # WHY (Phase A.28.1): Pass skip_smart=True so the per-TF compute
        #      path never calls smart_features.compute_smart_features.
        #      The frame here contains only {tf}_ columns — SMART
        #      features need cross-TF lookups and would fall back to
        #      zeros for every cross-TF column, emit a flood of
        #      _safe_col warnings, and produce garbage SMART columns
        #      that A.28 then has to strip on cache write. Cheaper
        #      and cleaner to simply not compute them here. SMART
        #      features are computed once on the final merged frame
        #      by run_comparison_matrix, which is the only place
        #      they can be computed correctly.
        # CHANGED: April 2026 — Phase A.28.1
        ind = indicator_utils.compute_indicators(
            candles, only=compute_groups, prefix=f"{tf}_", skip_smart=True
        )
        ind = ind.reset_index()   # timestamp index → 'timestamp' column
    else:
        ind = indicator_utils.compute_all_indicators(candles, prefix=f"{tf}_")
        # compute_all_indicators uses candles['timestamp'] as the DataFrame index.
        # reset_index() promotes it to a regular column named 'timestamp'.
        ind = ind.reset_index()

    # Defensive: ensure 'timestamp' column exists after reset_index
    # compute_all_indicators may use integer index → reset_index creates 'index' not 'timestamp'
    if 'timestamp' not in ind.columns:
        if 'index' in ind.columns:
            ind = ind.rename(columns={'index': 'timestamp'})
        elif len(candles) == len(ind):
            ind['timestamp'] = candles['timestamp'].values
        else:
            raise KeyError(f"Cannot find timestamp column after computing {tf} indicators. "
                           f"Columns: {list(ind.columns)[:10]}")

    ind['timestamp'] = normalize_timestamp(ind['timestamp'])
    ind = ind.dropna(subset=['timestamp']).reset_index(drop=True)

    # WHY (Phase A.28): Belt-and-braces — even when freshly computed
    #      via compute_indicators, no SMART_/REGIME_ column should
    #      land in the per-TF cache. Strip before writing so future
    #      loads can never inherit cross-TF features from a per-TF
    #      file. Pairs with the load-time strip above.
    # CHANGED: April 2026 — Phase A.28
    _bad_cols = [c for c in ind.columns
                 if c.startswith('SMART_') or c.startswith('REGIME_')]
    if _bad_cols:
        ind = ind.drop(columns=_bad_cols)

    ind.to_parquet(cache_path, index=False)
    log.info(f"  {tf}: {len(ind.columns) - 1} indicators cached -> {cache_path}")
    return ind


def build_multi_tf_indicators(data_dir, entry_timestamps, required_indicators=None, entry_tf=None):
    """
    Load and align all timeframe indicators onto the entry timeframe's timestamp spine.

    For each TF, uses merge_asof with direction='backward' so each entry candle
    receives the most recent indicator values from that TF.

    Look-ahead prevention: when entry_tf is provided, higher-TF indicator
    timestamps are shifted forward by one bar duration before the merge.
    This ensures merge_asof(backward) finds the previous COMPLETED bar,
    not the currently-forming one whose final values aren't yet available.

    required_indicators: optional dict {"M5": ["adx_14", "aroon_down", ...], ...}
        returned by _extract_required_indicators(). When provided, each TF only
        computes the indicators its rules actually use — dramatically faster for
        large datasets (e.g. M5 with 1.5M candles).

    Returns a single DataFrame indexed 0..len(entry_timestamps)-1 with all
    prefixed indicator columns (e.g. M5_rsi_14, H4_adx_14, D1_kst, …).
    """
    # WHY: This is NOT always H1 — it's whatever entry TF the user selected.
    entry_spine = pd.DataFrame({'timestamp': normalize_timestamp(pd.Series(entry_timestamps))})
    entry_spine['timestamp'] = entry_spine['timestamp'].astype('datetime64[ns]')
    entry_spine = entry_spine.sort_values('timestamp').reset_index(drop=True)

    combined = entry_spine.copy()

    for tf in _TIMEFRAMES:
        needed = required_indicators.get(tf) if required_indicators else None
        tf_ind = _load_tf_indicators(tf, data_dir, needed_indicators=needed)
        if tf_ind is None:
            continue
        assert len(tf_ind) > 0, \
            f"{tf} indicator DataFrame is empty after loading"
        tf_ind['timestamp'] = tf_ind['timestamp'].astype('datetime64[ns]')
        tf_ind = tf_ind.sort_values('timestamp').reset_index(drop=True)

        # Look-ahead prevention: shift higher-TF timestamps forward by one bar
        # duration so merge_asof(backward) finds the PREVIOUS COMPLETED bar,
        # not the forming one.  E.g. H1 bar at 10:00 → shifted to 11:00, so
        # an M15 entry at 10:00 picks up the H1 09:00 bar (completed).
        if entry_tf is not None:
            _TF_MIN = {'M1': 1, 'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240,
                        'D1': 1440, 'W1': 10080}
            entry_minutes = _TF_MIN.get(entry_tf, 60)
            tf_minutes = _TF_MIN.get(tf, 60)
            if tf_minutes > entry_minutes:
                tf_ind = tf_ind.copy()
                tf_ind['timestamp'] = tf_ind['timestamp'] + pd.Timedelta(minutes=tf_minutes)

        merged = pd.merge_asof(
            combined[['timestamp']],
            tf_ind,
            on='timestamp',
            direction='backward',
        )
        ind_cols = [c for c in merged.columns if c != 'timestamp']

        # WHY (Phase A.15): merged is up to 1.5M rows × ~15 indicator
        #      columns per TF. Default float64 = 8 bytes/cell → a single
        #      TF's slice can be ~180 MB, and the cumulative concat
        #      across 5 TFs blew past 3.5 GiB on M5 backtests, causing
        #      MemoryError before any backtest ran.
        #
        #      Indicator values (RSI, MA, ATR, ADX, MACD, BB widths,
        #      candle stats) are all bounded and fit comfortably within
        #      float32's ~7 decimal digits. ML feature matrices use
        #      float32 by default for exactly this reason. Rule
        #      comparisons (>, <=) against float64 thresholds in the
        #      rule dicts up-promote the operand to float64
        #      automatically, so the comparison itself runs at full
        #      precision — no exit decisions change.
        #
        #      Halves memory for the indicator matrix. Timestamps stay
        #      datetime64[ns].
        # CHANGED: April 2026 — Phase A.15
        _ind_block = merged[ind_cols]
        _numeric_cols = _ind_block.select_dtypes(include=['float64', 'float32', 'int64', 'int32']).columns
        if len(_numeric_cols) > 0:
            _ind_block = _ind_block.astype(
                {c: 'float32' for c in _numeric_cols},
                copy=False,
            )
        combined = pd.concat([combined, _ind_block], axis=1)

    combined = combined.drop(columns=['timestamp']).reset_index(drop=True)

    # WHY (Phase A.28): Defensive de-duplication. Even with the per-TF
    #      cache strip above, a stale parquet from before this phase
    #      may still have duplicate columns the first time the new
    #      hashed cache filename is built. And in general, two TFs
    #      could legitimately compute a column with the same prefixed
    #      name (e.g. both M5 and M15 emit M5_hour_of_day if a future
    #      bug were introduced). Either way: pandas df[col] returns a
    #      DataFrame instead of a Series on duplicates, fast_backtest
    #      builds an all-False mask, signals never fire. Take the FIRST
    #      occurrence of any duplicated name. Logged so the user sees
    #      it if it happens.
    # CHANGED: April 2026 — Phase A.28
    _dupes = combined.columns[combined.columns.duplicated(keep=False)]
    if len(_dupes) > 0:
        _dupe_set = sorted(set(_dupes.tolist()))
        log.warning(
            f"  [build_multi_tf_indicators] {len(_dupe_set)} duplicate "
            f"column name(s) — keeping FIRST occurrence: "
            f"{_dupe_set[:10]}{'...' if len(_dupe_set) > 10 else ''}"
        )
        combined = combined.loc[:, ~combined.columns.duplicated(keep='first')]

    return combined


def _count_swap_nights(entry_dt, exit_dt):
    """Count effective swap nights with FX/CFD Wednesday triple-roll.

    WHY: Forex and most CFD instruments apply 3× swap on Wednesday night
         to compensate for the Saturday + Sunday settlement days that are
         skipped on those days. Using raw calendar days therefore understates
         the true swap cost for any trade that spans a Wednesday.
    CHANGED: April 2026 — rollover-aware swap count
    """
    days = (exit_dt.date() - entry_dt.date()).days
    if days <= 0:
        return 0
    # Add 2 extra nights for every Wednesday crossed (Wednesday = weekday 2)
    import datetime as _dt
    extra = sum(
        2 for i in range(days)
        if (entry_dt.date() + _dt.timedelta(days=i)).weekday() == 2
    )
    return days + extra


# WHY: Real brokers charge different swap rates for long vs short
#      (gold longs are typically much more expensive than shorts —
#      Get Leveraged: long -45.5 pips/night, short -33.6).
#      Old code took a single swap_cost_per_lot_per_night value and
#      ignored it when not > 0, which meant every NEGATIVE swap (i.e.
#      every real broker swap) was treated as zero. Both bugs fixed:
#      pick by direction, allow negative values to flow through.
# CHANGED: April 2026 — asymmetric swap with per-direction values
def _select_swap_pips(direction, swap_long_pips_per_night,
                       swap_short_pips_per_night):
    """Return the swap-pip-per-night value matching the trade direction.

    Kept signed: negative = cost, positive = credit.
    """
    if direction is None:
        return 0.0
    d = str(direction).upper()
    if d == 'BUY':
        return float(swap_long_pips_per_night or 0)
    if d == 'SELL':
        return float(swap_short_pips_per_night or 0)
    return 0.0


def _vectorized_fixed_sltp_exits(df, signal_indices, signal_rule_ids, rules,
                                  exit_strategy, direction, pip_size,
                                  spread_pips, commission_pips, slippage_pips,
                                  account_size, risk_per_trade_pct,
                                  default_sl_pips, pip_value_per_lot,
                                  # WHY: Asymmetric swap — old single value ignored negatives
                                  #      and assumed long==short. Both bugs fixed.
                                  # CHANGED: April 2026 — asymmetric swap
                                  swap_long_pips_per_night=0,
                                  swap_short_pips_per_night=0,
                                  news_blackout_minutes=0,
                                  max_trades_per_day=0,
                                  leverage=0, contract_size=100.0,
                                  compound_equity=False,
                                  # WHY: variable_spread / max_spread_pips must reach this
                                  #      function so the MaxSpreadPips filter fires on the
                                  #      vectorized path. Without them the NameError at
                                  #      "if max_spread_pips > 0 and variable_spread" crashes
                                  #      every FixedSLTP backtest when the params are non-default.
                                  # CHANGED: April 2026 — fix NameError in vectorized path
                                  variable_spread=False,
                                  max_spread_pips=0,
                                  # WHY: Seed the per-entry slippage RNG so fast and slow
                                  #      paths use the same uniform distribution and are
                                  #      comparable on equal seeds. None = unseeded.
                                  # CHANGED: April 2026 — slippage symmetry fix
                                  slippage_seed=None):
    """
    Vectorized trade simulation for FixedSLTP exit strategy.

    WHY: The iterrows() loop processes ~150K candle iterations for ~3000 trades.
         For FixedSLTP, SL and TP are constant — we can find the exit candle
         with a single numpy operation per trade instead of looping.

    HOW: For each entry signal:
      1. Compute SL/TP prices (fixed from entry price)
      2. Get numpy arrays of future highs/lows
      3. Find first index where low <= SL or high >= TP
      4. Determine if SL or TP hit first (when both trigger on same candle)

    CHANGED: April 2026 — replaces iterrows for 10-50x speedup

    NOTE (T1b): SL-aware sizing is not needed here. This function is ONLY
          called when isinstance(exit_strategy, FixedSLTP) is True, and
          FixedSLTP always has sl_pips attribute. The lot sizing below
          reads exit_strategy.sl_pips directly — no ATR fallback needed.
    """
    trades = []
    # WHY: Running balance for compound equity.
    # CHANGED: April 2026 — equity-tracking lot sizing
    _running_balance = float(account_size) if account_size is not None else None

    # WHY: Slippage RNG — same uniform(0, slippage_pips) distribution as
    #      the slow path (run_backtest). Old fast path used constant
    #      slippage_pips which was 2× more pessimistic than slow path's
    #      average. Both paths now agree on the same distribution.
    # CHANGED: April 2026 — slippage symmetry fix
    _vect_slip_rng = random.Random(slippage_seed)

    # WHY: Normalize SL/TP prices to MT5's symbol-digits precision,
    #      matching MT5's NormalizeDouble on order placement. The slow
    #      path already does this via exit_strategies._normalize_price.
    #      Without normalization, sub-pip dust (e.g. 4275.001234) can
    #      cause the fast path to trigger SL when MT5 doesn't (or miss
    #      when MT5 hits) on candles where low ≈ SL.
    # CHANGED: April 2026 — SL/TP normalization parity fix
    import math as _vect_math
    _vect_decimals = (max(0, -int(_vect_math.floor(_vect_math.log10(pip_size))))
                      if pip_size > 0 else 2)

    sl_pips = exit_strategy.sl_pips
    tp_pips = exit_strategy.tp_pips
    # WHY (Phase A.28.2): Read max_candles off the strategy (None when
    #      unset, preserving old behavior for callers that did not pass
    #      it). The hot loop below caps the future-candle scan window
    #      at max_candles so a trade can not drift to END_OF_DATA in
    #      a sideways period and trigger the occupied_until_idx
    #      lockout that wipes out every subsequent signal.
    # CHANGED: April 2026 — Phase A.28.2
    _a282_max_candles = getattr(exit_strategy, 'max_candles', None)

    # Pre-extract numpy arrays (read-only, no copy)
    all_opens  = df['open'].values.astype(float)
    all_highs  = df['high'].values.astype(float)
    all_lows   = df['low'].values.astype(float)
    all_closes = df['close'].values.astype(float)
    all_times  = df['timestamp'].values

    index_positions = {idx: pos for pos, idx in enumerate(df.index)}
    occupied_until_idx = -1
    # WHY (Phase A.42): Per-day trade counter for max_trades_per_day.
    # CHANGED: April 2026 — Phase A.42
    _a42_daily_counts: dict = {}
    _a42_limit = int(max_trades_per_day) if max_trades_per_day and max_trades_per_day > 0 else 0

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue

        rule_id   = int(signal_rule_ids.loc[sig_idx])
        entry_pos = index_positions.get(sig_idx, 0)

        if entry_pos + 1 >= len(df):
            continue

        # WHY (Phase A.42): Enforce max trades per calendar day.
        # CHANGED: April 2026 — Phase A.42
        if _a42_limit > 0:
            try:
                _a42_day = str(pd.Timestamp(all_times[entry_pos + 1]).date())
                if _a42_daily_counts.get(_a42_day, 0) >= _a42_limit:
                    continue
            except Exception:
                pass

        # WHY: The for-loop path in run_backtest checks is_news_blackout
        #      before each entry, but this vectorized path was missing it.
        #      Any FixedSLTP strategy routed here bypassed the news filter
        #      entirely — every news blackout the user configured was
        #      silently ignored for the fastest execution path.
        # CHANGED: April 2026 — add news blackout check (audit HIGH)
        if news_blackout_minutes > 0:
            from project2_backtesting.news_calendar import is_news_blackout
            entry_time_check = pd.Timestamp(all_times[entry_pos + 1])
            # CHANGED: April 2026 — keyword arg with renamed param (Phase 21 Fix 6)
            if is_news_blackout(entry_time_check, blackout_half_window_minutes=news_blackout_minutes):
                continue

        entry_price = all_opens[entry_pos + 1]

        # WHY: Per-entry random slippage — same uniform(0, max) distribution
        #      as the slow path. Constant slippage was 2× too pessimistic.
        # CHANGED: April 2026 — slippage symmetry fix
        _slip_this_entry = (_vect_slip_rng.uniform(0, slippage_pips)
                            if slippage_pips > 0 else 0.0)

        # WHY: Old code added spread only to BUY entries. SELL entries
        #      receive the bid (open - spread/2), so spread should also
        #      cost the SELL trader — entry_price should be SUBTRACTED
        #      by spread_pips (making the SELL entry worse). Without
        #      this fix, SELL strategies look ~2 pips better than live.
        # CHANGED: April 2026 — fix SELL spread cost (audit Family #4)
        if direction == "BUY":
            entry_price += (spread_pips + _slip_this_entry) * pip_size
        else:
            entry_price -= (spread_pips + _slip_this_entry) * pip_size

        # WHY: MaxSpreadPips filter for vectorized path. Can't easily
        #      vary spread per entry in numpy, but CAN skip entries
        #      during wide-spread sessions.
        # CHANGED: April 2026 — max spread filter in vectorized path
        if max_spread_pips > 0 and variable_spread:
            _entry_spread = _get_session_spread(
                all_times[entry_pos + 1], spread_pips, variable_spread
            )
            if _entry_spread > max_spread_pips:
                continue
            # Use session spread for this entry
            if direction == "BUY":
                entry_price = all_opens[entry_pos + 1] + (_entry_spread + _slip_this_entry) * pip_size
            else:
                entry_price = all_opens[entry_pos + 1] - (_entry_spread + _slip_this_entry) * pip_size

        entry_time = all_times[entry_pos + 1]

        # WHY: Normalize entry/SL/TP to MT5 symbol-digits precision
        #      (NormalizeDouble parity). Sub-pip dust on raw floats causes
        #      rare SL/TP mismatches vs MT5 on candles where low/high ≈ SL/TP.
        #      Entry is normalized first so SL/TP derived from it are clean.
        # CHANGED: April 2026 — entry/SL/TP normalization parity fix
        entry_price = round(entry_price, _vect_decimals)
        if direction == "BUY":
            sl_price = round(entry_price - sl_pips * pip_size, _vect_decimals)
            tp_price = round(entry_price + tp_pips * pip_size, _vect_decimals)
        else:
            sl_price = round(entry_price + sl_pips * pip_size, _vect_decimals)
            tp_price = round(entry_price - tp_pips * pip_size, _vect_decimals)

        # WHY: Old code set start two positions after the signal, skipping
        #      the entry candle entirely. But entry happens at the OPEN of
        #      (entry_pos + 1), and same-bar SL/TP hits happen within
        #      that same candle's high/low range. Starting at +2 misses
        #      those — fast scalp exits were reported one bar too late.
        # CHANGED: April 2026 — start scan at entry candle (audit HIGH)
        start = entry_pos + 1
        if start >= len(df):
            continue

        future_highs = all_highs[start:]
        future_lows  = all_lows[start:]
        future_opens = all_opens[start:]

        # WHY (Phase A.28.2): Cap the search window at max_candles. Old
        #      code scanned all future candles → trades that never hit
        #      SL/TP within the test window dragged to END_OF_DATA and
        #      tripped the occupied_until_idx lockout, killing every
        #      subsequent signal. Slicing here is cheap (numpy view, no
        #      copy) and gives FixedSLTP the same hold ceiling that
        #      TrailingStop/ATRBased already enforce internally.
        # CHANGED: April 2026 — Phase A.28.2
        if _a282_max_candles is not None and len(future_highs) > _a282_max_candles:
            future_highs = future_highs[:_a282_max_candles]
            future_lows  = future_lows[:_a282_max_candles]
            future_opens = future_opens[:_a282_max_candles]

        # ── Find exit candle with numpy ──────────────────────────────────
        # WHY: Instead of looping candle-by-candle, we check ALL future candles
        #      at once. numpy finds the first match in microseconds.
        if direction == "BUY":
            sl_hit = future_lows  <= sl_price
            tp_hit = future_highs >= tp_price
        else:
            sl_hit = future_highs >= sl_price
            tp_hit = future_lows  <= tp_price

        either_hit = sl_hit | tp_hit

        if either_hit.any():
            exit_offset = int(np.argmax(either_hit))
            exit_pos    = start + exit_offset

            candle_open = future_opens[exit_offset]
            candle_low  = future_lows[exit_offset]
            candle_high = future_highs[exit_offset]

            sl_triggered = bool(sl_hit[exit_offset])
            tp_triggered = bool(tp_hit[exit_offset])

            if sl_triggered and tp_triggered:
                # Both on same candle — gap check first, then conservative SL
                if direction == "BUY":
                    if candle_open <= sl_price:
                        exit_price  = candle_open
                        exit_reason = "STOP_LOSS_GAP"
                    elif candle_open >= tp_price:
                        exit_price  = candle_open
                        exit_reason = "TAKE_PROFIT_GAP"
                    else:
                        exit_price  = sl_price
                        exit_reason = "STOP_LOSS"
                else:
                    if candle_open >= sl_price:
                        exit_price  = candle_open
                        exit_reason = "STOP_LOSS_GAP"
                    elif candle_open <= tp_price:
                        exit_price  = candle_open
                        exit_reason = "TAKE_PROFIT_GAP"
                    else:
                        exit_price  = sl_price
                        exit_reason = "STOP_LOSS"
            elif sl_triggered:
                if direction == "BUY" and candle_open <= sl_price:
                    exit_price  = candle_open
                    exit_reason = "STOP_LOSS_GAP"
                elif direction == "SELL" and candle_open >= sl_price:
                    exit_price  = candle_open
                    exit_reason = "STOP_LOSS_GAP"
                else:
                    exit_price  = sl_price
                    exit_reason = "STOP_LOSS"
            else:
                if direction == "BUY" and candle_open >= tp_price:
                    exit_price  = candle_open
                    exit_reason = "TAKE_PROFIT_GAP"
                elif direction == "SELL" and candle_open <= tp_price:
                    exit_price  = candle_open
                    exit_reason = "TAKE_PROFIT_GAP"
                else:
                    exit_price  = tp_price
                    exit_reason = "TAKE_PROFIT"

            exit_time    = all_times[exit_pos]
            candles_held = exit_offset + 1
        else:
            # No SL/TP hit within the (possibly max_candles-capped)
            # search window — exit at the last candle of the search
            # window, not the last candle of the dataset. With
            # max_candles=1000, the typical case is FIXED_MAX_CANDLES
            # at start+1000, NOT END_OF_DATA at len(df)-1. END_OF_DATA
            # only occurs for trades opened within max_candles of the
            # dataset end.
            # WHY (Phase A.28.2): Old code set exit_pos = len(df) - 1
            #      unconditionally → occupied_until_idx jumped to the
            #      end of the dataset and locked out every subsequent
            #      signal. New code uses start + len(future_highs) - 1
            #      which is the actual exit position (capped by
            #      max_candles when applicable, otherwise still the
            #      true last candle).
            # CHANGED: April 2026 — Phase A.28.2
            exit_pos = start + len(future_highs) - 1
            if exit_pos >= len(df):
                exit_pos = len(df) - 1
            # WHY: MT5 TimeBased exit fires on the first tick of candle N+1,
            #      which is effectively the OPEN of the next bar. Python was
            #      using CLOSE of candle N. Use OPEN of N+1 to match MT5.
            # CHANGED: April 2026 — match MT5 TimeBased exit convention
            if exit_pos + 1 < len(all_opens):
                exit_price = all_opens[exit_pos + 1]
            else:
                exit_price = all_closes[exit_pos]  # end of data fallback
            exit_time  = all_times[exit_pos]
            if _a282_max_candles is not None and (exit_pos - start + 1) >= _a282_max_candles:
                exit_reason = "FIXED_MAX_CANDLES"
            else:
                exit_reason = "END_OF_DATA"
            candles_held = len(future_highs)

        # P&L
        if direction == "BUY":
            pnl_pips = (exit_price - entry_price) / pip_size
        else:
            pnl_pips = (entry_price - exit_price) / pip_size

        net_pips = pnl_pips - commission_pips

        # WHY: Real broker swaps are negative (cost) and asymmetric.
        #      Old code skipped negatives so all real swaps were zero.
        # CHANGED: April 2026 — asymmetric, sign-preserving swap
        swap_per_night = _select_swap_pips(
            direction, swap_long_pips_per_night, swap_short_pips_per_night)
        swap_nights    = 0
        swap_cost_pips = 0.0
        if swap_per_night != 0:
            entry_dt    = pd.Timestamp(entry_time)
            exit_dt     = pd.Timestamp(exit_time)
            swap_nights = _count_swap_nights(entry_dt, exit_dt)
            if swap_nights > 0:
                # swap_per_night is already in pips/night — signed.
                # ADD to net_pips (not subtract): negative swap reduces P/L.
                swap_cost_pips = swap_nights * swap_per_night
                net_pips += swap_cost_pips

        # Lot sizing
        # WHY: Old code used default_sl_pips (=150) for every strategy.
        #      But in the vectorized FixedSLTP path we KNOW the real SL
        #      from exit_strategy.sl_pips (already extracted as sl_pips
        #      at top of function). Using the actual SL gives correct
        #      per-strategy lot sizing — strategies with wider SL get
        #      smaller lots, narrower SL get bigger lots, all sized to
        #      risk_per_trade_pct of the account.
        # CHANGED: April 2026 — use actual sl_pips for lot sizing (audit Family #2)
        lot_size = 0.01
        if account_size and risk_per_trade_pct > 0 and sl_pips > 0:
            # WHY: When compound_equity=True, use running balance for sizing.
            # CHANGED: April 2026 — equity-tracking lot sizing
            _sizing_equity = _running_balance if (compound_equity and _running_balance) else account_size
            risk_dollars = _sizing_equity * (risk_per_trade_pct / 100)
            lot_size = max(0.01, round(risk_dollars / (sl_pips * pip_value_per_lot), 2))
            # WHY (leverage): Cap lot_size to what the account can margin.
            #      A $10K account at 1:10 on XAUUSD (~$3300/oz, 100 oz/lot)
            #      can hold max ~0.30 lots. Without this cap the backtest
            #      counts trades the broker would physically reject.
            # CHANGED: April 2026 — margin-aware lot sizing
            if leverage > 0 and entry_price > 0:
                margin_per_lot = (contract_size * entry_price) / leverage
                max_lots_by_margin = (_sizing_equity * 0.95) / margin_per_lot
                if lot_size > max_lots_by_margin:
                    lot_size = max(0.01, round(max_lots_by_margin, 2))

        net_profit = net_pips * pip_value_per_lot * lot_size

        # WHY: Update running balance for compound equity.
        # CHANGED: April 2026 — equity-tracking lot sizing
        if compound_equity and _running_balance is not None and account_size:
            _running_balance = max(account_size * 0.5, _running_balance + net_profit)

        # WHY (Phase A.42): Increment daily counter after a trade opens.
        # CHANGED: April 2026 — Phase A.42
        if _a42_limit > 0:
            try:
                _a42_entry_day = str(pd.Timestamp(entry_time).date())
                _a42_daily_counts[_a42_entry_day] = _a42_daily_counts.get(_a42_entry_day, 0) + 1
            except Exception:
                pass

        trades.append({
            'entry_time':   str(entry_time),
            'exit_time':    str(exit_time),
            'entry_price':  round(float(entry_price), 5),
            'exit_price':   round(float(exit_price), 5),
            'direction':    direction,
            'pips':         round(float(pnl_pips + commission_pips), 1),
            'net_pips':     round(float(net_pips), 1),
            'net_profit':   round(float(net_profit), 2),
            'lot_size':     lot_size,
            'exit_reason':  exit_reason,
            'candles_held': candles_held,
            'rule_id':      rule_id,
            # WHY: Per-trade cost breakdown for the diagnostic.
            #      Signed: spread/commission always negative (cost),
            #      swap negative for typical broker, positive if credit.
            # CHANGED: April 2026 — cost breakdown
            'cost_spread_pips':     round(-float(spread_pips), 1),
            'cost_commission_pips': round(-float(commission_pips), 1),
            'cost_swap_pips':       round(float(swap_cost_pips), 1),
            'swap_nights':          int(swap_nights),
        })

        occupied_until_idx = df.index[exit_pos]

    return trades


def run_backtest(candles_df, indicators_df, rules, exit_strategy,
                 direction="BUY", start_date=None, end_date=None,
                 pip_size=0.01, max_open_trades=1,
                 spread_pips=2.5, commission_pips=0.0,
                 slippage_pips=0.0,
                 # WHY (Phase 35 Fix 3): Old code used unseeded
                 #      random.uniform for slippage — two runs with the
                 #      same inputs gave different results. Accept an
                 #      optional seed so reproducible runs are possible.
                 #      Default None = unseeded (backward compat with
                 #      existing callers).
                 # CHANGED: April 2026 — Phase 35 Fix 3 — optional seed
                 #          (audit Part C MED #20)
                 slippage_seed=None,
                 account_size=None, risk_per_trade_pct=1.0,
                 default_sl_pips=150.0, pip_value_per_lot=1.0,
                 # WHY: Asymmetric swap — see _select_swap_pips.
                 # CHANGED: April 2026 — asymmetric swap
                 swap_long_pips_per_night=0.0,
                 swap_short_pips_per_night=0.0,
                 news_blackout_minutes=0,
                 # WHY (Phase A.42): 0 = no limit; positive int = max trades
                 #      per calendar day, matching live EA's MaxTradesPerDay.
                 # CHANGED: April 2026 — Phase A.42
                 max_trades_per_day=0,
                 # WHY (leverage): 0 = no margin check (backward compat).
                 #      When > 0, lot size is capped so margin ≤ 95% of equity.
                 #      contract_size is the number of base units per lot
                 #      (100 oz for XAUUSD, 100000 for FX pairs).
                 # CHANGED: April 2026 — margin-aware lot sizing
                 leverage=0, contract_size=100.0,
                 # WHY: Static account_size means no compounding — after
                 #      winning $200 you still risk based on $10,000. With
                 #      compound_equity=True, lot sizing uses running balance
                 #      so winners make subsequent trades larger, accelerating
                 #      toward the eval profit target.
                 # CHANGED: April 2026 — equity-tracking lot sizing
                 compound_equity=False,
                 # WHY: hard_close_hour forces all positions closed at a
                 #      specific GMT hour (0-23). -1 = disabled (backward
                 #      compat). Matches the EA's DailyResetHourGMT / prop firm
                 #      rules that require no overnight holds.
                 # CHANGED: April 2026 — hard close hour (backtester parity)
                 hard_close_hour=-1,
                 # WHY: cooldown_candles blocks new entries for N candles after
                 #      a trade closes. The EA's CooldownMinutes does the same.
                 #      0 = no cooldown (backward compat, pre-phase behaviour).
                 # CHANGED: April 2026 — cooldown between trades (MT5 parity)
                 cooldown_candles=0,
                 # WHY: variable_spread applies session-based spread multipliers
                 #      instead of fixed spread. max_spread_pips skips entries
                 #      when spread exceeds limit (matches EA's MaxSpreadPips).
                 #      Both default to backward-compat off state.
                 # CHANGED: April 2026 — session-based variable spread model
                 variable_spread=False,
                 max_spread_pips=0,
                 # WHY: data_dir path lets the exit strategy resolve intra-candle
                 #      ambiguity using tick data when available. None = disabled.
                 # CHANGED: April 2026 — tick data for exit ambiguity resolution
                 data_dir=None):
    """
    Run a single backtest using vectorized entry detection.

    1. Build a boolean mask over the full indicator DataFrame to find all signal candles.
    2. Loop only over signal candles (~50-500) to simulate individual trade exits.

    Returns list of trade dicts.
    """
    trades = []
    # WHY: Running balance for compound equity — updated after each trade.
    #      Clamped to 50% of starting capital as a safety floor.
    # CHANGED: April 2026 — equity-tracking lot sizing
    _running_balance = float(account_size) if account_size is not None else None

    # WHY: Infer candle duration once per call for tick window sizing.
    #      Median of first 10 gaps is robust against weekend/session gaps.
    # CHANGED: April 2026 — tick data candle duration
    _run_candle_tf_minutes = 60  # default H1
    try:
        _ts_sample = pd.to_datetime(candles_df['timestamp'].iloc[:11])
        _gaps = [max(1, int((_ts_sample.iloc[i+1] - _ts_sample.iloc[i]).total_seconds() / 60))
                 for i in range(len(_ts_sample) - 1)]
        if _gaps:
            import statistics
            _run_candle_tf_minutes = int(statistics.median(_gaps))
    except Exception:
        pass

    # WHY: Drop duplicate candle timestamps before any further processing.
    #      Raw CSVs with duplicate bars produce corrupted rolling indicators.
    #      Defense-in-depth dedup, matching backtest_engine.run_backtest.
    #      fast_backtest is excluded — its caller is responsible for dedup.
    # CHANGED: April 2026 — drop duplicate timestamps (audit HIGH)
    if 'timestamp' in candles_df.columns:
        _dedup_count = len(candles_df) - candles_df['timestamp'].nunique()
        if _dedup_count > 0:
            log.info(f"  [strategy_backtester] Dropping {_dedup_count} duplicate candle timestamps")
            candles_df = candles_df.drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
            if 'timestamp' in indicators_df.columns:
                indicators_df = indicators_df.drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)

    # ── Date filter ──────────────────────────────────────────────────────────
    df  = candles_df.copy().reset_index(drop=True)
    ind = indicators_df.copy().reset_index(drop=True)

    # Ensure same length before filtering
    min_len = min(len(df), len(ind))
    if len(df) != len(ind):
        log.warning(f"  [run_backtest] candles ({len(df)}) and indicators ({len(ind)}) length mismatch — trimming to {min_len}")
        df  = df.iloc[:min_len]
        ind = ind.iloc[:min_len]

    if start_date is not None:
        m = df['timestamp'] >= pd.to_datetime(start_date)
        df  = df[m]
        ind = ind.loc[df.index]
    if end_date is not None:
        m = df['timestamp'] <= pd.to_datetime(end_date)
        df  = df[m]
        ind = ind.loc[df.index]

    # Skip warmup (first 200 candles for indicator stability)
    if len(df) > 200:
        df  = df.iloc[200:]
        ind = ind.loc[df.index]

    if len(df) == 0:
        return trades

    # ── Compute SMART & REGIME features if rules need them and they're not already present ───
    smart_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                    if c['feature'].startswith('SMART_')}
    regime_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                     if c['feature'].startswith('REGIME_')}

    # Only compute SMART features if not already present (computed once in run_comparison_matrix)
    if smart_needed and not any(c.startswith('SMART_') for c in ind.columns):
        # SMART features needed but not in indicators_df — compute them now
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            # SMART features need hour_of_day and open_time columns
            if 'hour_of_day' not in ind.columns:
                ind['hour_of_day'] = df['timestamp'].dt.hour
            if 'open_time' not in ind.columns:
                ind['open_time'] = df['timestamp'].astype(str)

            ind = _add_tf_divergences(ind)
            ind = _add_indicator_dynamics(ind)
            ind = _add_alignment_scores(ind)
            ind = _add_session_intelligence(ind)
            ind = _add_volatility_regimes(ind)
            ind = _add_price_action(ind)
            ind = _add_momentum_quality(ind)

            smart_cols = [c for c in ind.columns if c.startswith('SMART_')]
            log.info(f"  [run_backtest] Computed {len(smart_cols)} SMART features")
        except ImportError:
            log.warning("smart_features module not found — SMART conditions will not match")
        except Exception as e:
            log.warning(f"Error computing SMART features: {e}")

    # Compute REGIME features if needed
    if regime_needed and not any(c.startswith('REGIME_') for c in ind.columns):
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            ind = _add_regime_features(ind)
            regime_cols = [c for c in ind.columns if c.startswith('REGIME_')]
            log.info(f"  [run_backtest] Computed {len(regime_cols)} REGIME features")
        except ImportError:
            log.warning("smart_features module not found — REGIME conditions will not match")
        except Exception as e:
            log.warning(f"Failed to compute SMART features: {e}")

    # ── VECTORIZED: build entry signal mask ──────────────────────────────────
    signal_mask     = pd.Series(False, index=ind.index)
    signal_rule_ids = pd.Series(-1,    index=ind.index, dtype=int)

    # WHY (Phase A.24): the previous pandas-Series-based mask building
    #      pattern (rule_mask &= col_data <op> val) crashed with
    #      "'NotImplementedType' object has no attribute '_indexed_same'"
    #      when ind[col] returned a DataFrame (duplicate column names in
    #      the multi-TF indicator merge), or when the column's dtype
    #      caused the comparison to return NotImplemented. The numpy
    #      path below cannot trigger _indexed_same because numpy arrays
    #      have no index. Diagnostic logging surfaces every coercion
    #      and anomaly so the underlying root cause is visible.
    # CHANGED: April 2026 — Phase A.24
    _ind_n = len(ind)

    # Pre-flight: detect duplicate column names in the indicators frame
    _dup_cols = ind.columns[ind.columns.duplicated()].tolist()
    if _dup_cols:
        log.warning(
            f"  [run_backtest] indicators frame has {len(_dup_cols)} duplicate "
            f"column names: {_dup_cols[:10]}{'...' if len(_dup_cols) > 10 else ''}. "
            f"This is the most likely cause of past _indexed_same crashes. "
            f"Each duplicate column will be collapsed to its first occurrence."
        )
        # De-duplicate by taking the first occurrence of each name
        ind = ind.loc[:, ~ind.columns.duplicated()]

    for rule_idx, rule in enumerate(rules):
        rule_mask_np = np.ones(_ind_n, dtype=bool)
        valid_rule   = True

        for cond in rule.get("conditions", []):
            col = cond["feature"]
            if col not in ind.columns:
                valid_rule = False
                break

            # Extract the column as a numpy float array.
            # If ind[col] returned a DataFrame for any reason (which
            # shouldn't happen after de-dup above but is defensive),
            # take the first sub-column.
            _raw = ind[col]
            if isinstance(_raw, pd.DataFrame):
                log.warning(
                    f"  [run_backtest] ind[{col!r}] returned a DataFrame "
                    f"with shape {_raw.shape}; taking first column."
                )
                _raw = _raw.iloc[:, 0]

            try:
                col_arr = pd.to_numeric(_raw, errors='coerce').to_numpy(dtype=float, copy=False)
            except Exception as _coerce_err:
                log.warning(
                    f"  [run_backtest] could not coerce column {col!r} to numeric "
                    f"({type(_raw).__name__}, dtype={getattr(_raw, 'dtype', '?')}): "
                    f"{_coerce_err!r} — rule skipped."
                )
                valid_rule = False
                break

            try:
                _val_f = float(cond["value"])
            except Exception:
                log.warning(
                    f"  [run_backtest] rule {rule_idx} has non-numeric value "
                    f"{cond.get('value')!r} on feature {col!r} — rule skipped."
                )
                valid_rule = False
                break

            op = cond["operator"]
            # numpy comparisons of float arrays vs scalar ALWAYS return
            # bool arrays — they cannot return NotImplemented.
            with np.errstate(invalid='ignore'):
                if op == "<=":
                    cond_arr = col_arr <= _val_f
                elif op == ">":
                    cond_arr = col_arr >  _val_f
                elif op == "<":
                    cond_arr = col_arr <  _val_f
                elif op == ">=":
                    cond_arr = col_arr >= _val_f
                elif op == "==":
                    cond_arr = col_arr == _val_f
                elif op == "!=":
                    cond_arr = col_arr != _val_f
                else:
                    log.warning(
                        f"  [run_backtest] Unknown operator {op!r} on feature "
                        f"{col!r} — rule skipped. Supported: <=, >, <, >=, ==, !="
                    )
                    valid_rule = False
                    break

            # NaN/inf values become False — no signal if indicator is invalid
            cond_arr = np.where(np.isnan(col_arr) | np.isinf(col_arr), False, cond_arr)
            rule_mask_np &= cond_arr

        if not valid_rule:
            continue

        # Convert numpy mask back to Series for downstream code
        rule_mask = pd.Series(rule_mask_np, index=ind.index)

        # First rule wins per candle
        new_signals = rule_mask & ~signal_mask
        signal_mask |= rule_mask
        signal_rule_ids[new_signals] = rule_idx

    # ── Phase A.38a / A.43: Regime filter gating ───────────────────────
    # WHY (Phase A.38a): If the user enabled the regime filter (A.36)
    #      and discovery produced a subset (A.37 / A.37.2), apply it
    #      here as a boolean mask AND'd into signal_mask. Signals at
    #      wrong-regime candles are blocked at evaluation time.
    # WHY (Phase A.43): Rules saved while the filter was active carry
    #      their discovery-time conditions under key 'regime_filter'.
    #      Use those as an override so the backtest always reproduces
    #      the exact regime context of discovery.
    # CHANGED: April 2026 — Phase A.38a / A.43
    try:
        from project2_backtesting.regime_filter_runtime import (
            build_regime_pass_mask, log_filter_summary_once,
        )
        # WHY (Code Audit Fix — Bug 3c): Distinguish three cases:
        #   (a) rules have no 'regime_filter' key (old rules) → None →
        #       fall back to global config (backward compat)
        #   (b) rules have key with conditions (filter ON at discovery)
        #       → use those conditions
        #   (c) rules have key but value is None/[] (filter OFF) → [] →
        #       explicitly suppress filtering regardless of global config
        _a43_rule_rf = None
        for _r in rules:
            _rf = _r.get('regime_filter')
            if _rf and isinstance(_rf, list) and len(_rf) > 0:
                _a43_rule_rf = _rf
                break
        _a43_has_key = any('regime_filter' in _r for _r in rules)
        if _a43_has_key and _a43_rule_rf is None:
            _a43_override = []   # new rule, filter was OFF at discovery
        else:
            _a43_override = _a43_rule_rf  # conditions or None (old rule)
        _a38a_regime_mask, _a38a_info = build_regime_pass_mask(
            ind, rule_action=direction, override_conditions=_a43_override,
        )
        if _a38a_info.get('enabled'):
            log_filter_summary_once(_a38a_info, source_label='run_backtest')
            _pre_count = int(signal_mask.sum())
            signal_mask = signal_mask & pd.Series(_a38a_regime_mask, index=ind.index)
            _post_count = int(signal_mask.sum())
            if _pre_count > 0:
                log.debug(
                    f"[A.38a/run_backtest] signals: {_pre_count} -> {_post_count} "
                    f"after regime filter ({_post_count / max(_pre_count, 1) * 100:.1f}% kept)"
                )
    except Exception as _a38a_e:
        log.warning(
            f"[A.38a/run_backtest] regime filter failed — proceeding without it: "
            f"{type(_a38a_e).__name__}: {_a38a_e}"
        )

    signal_indices = df.index[signal_mask].tolist()

    # WHY (Phase 35 Fix 3c): Create a local RNG for slippage so seeded
    #      runs are reproducible without contaminating global random
    #      state. slippage_seed=None means unseeded (matches old
    #      behavior). slippage_seed=int enables reproducible runs.
    # CHANGED: April 2026 — Phase 35 Fix 3c — per-run RNG
    _slip_rng = random.Random(slippage_seed)

    # ── Use vectorized exit for FixedSLTP (10-50x faster) ────────────────────
    # WHY: FixedSLTP has constant SL/TP levels — numpy finds the exit candle
    #      in microseconds per trade vs milliseconds for the iterrows loop.
    # CHANGED: April 2026 — vectorized FixedSLTP path
    from project2_backtesting.exit_strategies import FixedSLTP, ATRFixedSLTP
    # WHY: ATRFixedSLTP has per-trade SL/TP (set in on_entry from ATR).
    #      The vectorized path assumes constant SL/TP across all trades.
    #      ATRFixedSLTP must use the iterative run_backtest path instead.
    # CHANGED: April 2026 — exclude ATRFixedSLTP from vectorized path
    if isinstance(exit_strategy, FixedSLTP) and not isinstance(exit_strategy, ATRFixedSLTP) and signal_indices:
        return _vectorized_fixed_sltp_exits(
            df, signal_indices, signal_rule_ids, rules,
            exit_strategy, direction, pip_size,
            spread_pips, commission_pips, slippage_pips,
            account_size, risk_per_trade_pct,
            default_sl_pips, pip_value_per_lot,
            swap_long_pips_per_night=swap_long_pips_per_night,
            swap_short_pips_per_night=swap_short_pips_per_night,
            news_blackout_minutes=news_blackout_minutes,
            max_trades_per_day=max_trades_per_day,
            leverage=leverage, contract_size=contract_size,
            compound_equity=compound_equity,
            variable_spread=variable_spread,
            max_spread_pips=max_spread_pips,
            slippage_seed=slippage_seed,
        )

    # ── Simulate trades from signal candles ──────────────────────────────────
    occupied_until_idx = -1   # index of last candle in current open trade
    _last_exit_pos     = -1   # integer position of last exit (for cooldown)
    # WHY (Phase A.42): Per-day trade counter for max_trades_per_day.
    # CHANGED: April 2026 — Phase A.42
    _a42_daily_counts_rb: dict = {}
    _a42_limit_rb = int(max_trades_per_day) if max_trades_per_day and max_trades_per_day > 0 else 0

    # Build positional lookup once (integer positions for slicing forward)
    index_positions = {idx: pos for pos, idx in enumerate(df.index)}

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue
        # WHY: Cooldown prevents back-to-back entries. The EA's CooldownMinutes
        #      blocks re-entry for N minutes after a trade closes. We model
        #      this as N candles since bar data has no per-minute resolution.
        # CHANGED: April 2026 — cooldown between trades (MT5 parity)
        if cooldown_candles > 0 and _last_exit_pos >= 0:
            _sig_pos = index_positions.get(sig_idx, 0)
            if _sig_pos <= _last_exit_pos + cooldown_candles:
                continue

        rule_id       = int(signal_rule_ids.loc[sig_idx])
        entry_pos_int = index_positions.get(sig_idx, 0)

        # Enter at the NEXT candle's open to avoid look-ahead bias
        if entry_pos_int + 1 >= len(df):
            continue
        next_candle = df.iloc[entry_pos_int + 1]

        # WHY (Phase A.42): Enforce max trades per calendar day.
        # CHANGED: April 2026 — Phase A.42
        if _a42_limit_rb > 0:
            try:
                _a42_day_rb = str(pd.Timestamp(next_candle['timestamp']).date())
                if _a42_daily_counts_rb.get(_a42_day_rb, 0) >= _a42_limit_rb:
                    continue
            except Exception:
                pass

        # News blackout filter
        if news_blackout_minutes > 0:
            from project2_backtesting.news_calendar import is_news_blackout
            entry_time = next_candle['timestamp']
            # CHANGED: April 2026 — keyword arg with renamed param (Phase 21 Fix 6)
            if is_news_blackout(entry_time, blackout_half_window_minutes=news_blackout_minutes):
                continue  # skip this entry

        # Determine direction first (needed for slippage sign)
        # WHY (Phase A.30): Old code read rule_obj.get("direction", "BUY")
        #      but the field is written as "action" by every rule
        #      producer in the codebase — step6_extract_rules at line
        #      ~376, analyze.py extract_rules after Phase A.27, and
        #      bot_entry_discovery. The "direction" key has never
        #      existed on a rule. So when direction=="BOTH" was
        #      passed, this branch always silently fell back to BUY.
        #
        #      Fix: read the correct key. With A.30's per-combo
        #      direction expansion in run_comparison_matrix, this
        #      branch is now a defensive fallback for legacy callers
        #      that still pass direction="BOTH" explicitly — but the
        #      bug was real and worth killing regardless.
        # CHANGED: April 2026 — Phase A.30
        if direction == "BOTH":
            rule_obj  = rules[rule_id] if rule_id < len(rules) else {}
            _action   = str(rule_obj.get("action", "BUY")).upper().strip()
            if _action in ('BUY', 'LONG'):
                trade_dir = "BUY"
            elif _action in ('SELL', 'SHORT'):
                trade_dir = "SELL"
            else:
                # action="BOTH" or unknown → conservative default
                trade_dir = "BUY"
        else:
            trade_dir = direction

        entry_price = float(next_candle["open"])
        # Apply random slippage against the trader (always a worse fill)
        if slippage_pips > 0:
            # WHY: Use per-run RNG initialized above for reproducibility.
            # CHANGED: April 2026 — Phase 35 Fix 3d — seeded slip
            slip = _slip_rng.uniform(0, slippage_pips) * pip_size
            if trade_dir == "BUY":
                entry_price += slip   # buy fills higher
            else:
                entry_price -= slip   # sell fills lower
        # WHY: Session-based spread models real broker behavior.
        #      Asian session spread is 1.5-2x wider than London.
        # CHANGED: April 2026 — variable spread model
        _trade_spread = _get_session_spread(
            next_candle["timestamp"], spread_pips, variable_spread
        )

        # WHY: MaxSpreadPips filter — skip entry when spread is too wide.
        #      Matches MT5 EA's MaxSpreadPips check.
        # CHANGED: April 2026 — max spread filter
        if max_spread_pips > 0 and _trade_spread > max_spread_pips:
            continue

        if trade_dir == "BUY":
            entry_price += _trade_spread * pip_size
        else:
            entry_price -= _trade_spread * pip_size
        entry_time = next_candle["timestamp"]

        pos = {
            "entry_price":         entry_price,
            "entry_time":          entry_time,
            "direction":           trade_dir,
            "highest_since_entry": float(next_candle["high"]),
            "lowest_since_entry":  float(next_candle["low"]),
            "candles_held":        0,
            "current_pnl_pips":    0,
            "rule_id":             rule_id,
        }

        if hasattr(exit_strategy, 'on_entry'):
            # WHY (Phase 35 Fix 6): Old code passed ind.loc[next_idx]
            #      — the ENTRY candle's indicator values — to on_entry.
            #      But entry happens at the OPEN of the entry candle;
            #      the entry candle's close-based indicators (H1_atr_14,
            #      etc.) haven't been computed yet at signal time.
            #      That's subtle look-ahead for ATR-based exits.
            #      Use the SIGNAL candle's indicators (ind.iloc[entry_pos_int])
            #      which were actually available when the rule fired.
            #      Price data stays from next_candle (that IS where
            #      the fill happens).
            # CHANGED: April 2026 — Phase 35 Fix 6 — signal-candle indicators
            #          (audit Part C MED #24)
            candle_dict = next_candle.to_dict()   # price at entry candle
            if 0 <= entry_pos_int < len(ind.index):
                signal_idx = ind.index[entry_pos_int]
                candle_dict.update(ind.loc[signal_idx].to_dict())   # indicators from SIGNAL bar
            exit_strategy.on_entry(candle_dict)

        # WHY: Provide tick and M1 loaders to exit strategies.
        #      Fallback chain: ticks → M1 → conservative.
        # CHANGED: April 2026 — tick data + M1 fallback in position info
        if data_dir and _check_ticks_available(data_dir):
            _d, _cm = data_dir, _run_candle_tf_minutes
            pos['_tick_loader'] = lambda ts, _d=_d, _cm=_cm: _load_ticks_for_candle(_d, ts, _cm)
        else:
            pos['_tick_loader'] = None
        if data_dir:
            _d, _cm = data_dir, _run_candle_tf_minutes
            pos['_m1_loader'] = lambda ts, _d=_d, _cm=_cm: _load_m1_for_candle(_d, ts, _cm)
        else:
            pos['_m1_loader'] = None

        # WHY (same-bar exit bias fix): pos["highest_since_entry"] is seeded
        #      from next_candle (the entry candle, df.iloc[entry_pos_int+1]).
        #      Starting remaining_df at +1 meant the first iteration processed
        #      that same candle, updated highest/lowest (idempotent), then
        #      called on_new_candle — which could trigger a trailing-stop exit
        #      on the entry bar itself: pure look-ahead bias.
        #      Starting at +2 skips the entry candle; earliest exit is the
        #      candle AFTER entry (candles_held=1). This matches fast_backtest.
        # CHANGED: April 2026 — same-bar exit look-ahead bias fix
        remaining_df = df.iloc[entry_pos_int + 2:]

        exit_price  = None
        exit_time   = None
        exit_reason = None
        candles_held = 0

        for future_idx, future_candle in remaining_df.iterrows():
            candles_held += 1
            pos["candles_held"]        = candles_held
            pos["highest_since_entry"] = max(pos["highest_since_entry"], float(future_candle["high"]))
            pos["lowest_since_entry"]  = min(pos["lowest_since_entry"],  float(future_candle["low"]))

            pnl = (float(future_candle["close"]) - entry_price) / pip_size
            if trade_dir == "SELL":
                pnl = -pnl
            pos["current_pnl_pips"] = pnl

            candle_dict = future_candle.to_dict()
            if future_idx in ind.index:
                candle_dict.update(ind.loc[future_idx].to_dict())

            # WHY: hard_close_hour forces all positions closed at a specific GMT
            #      hour. The EA does this to match prop firm rules or to avoid
            #      overnight holds. Without this, backtester lets trades hold
            #      indefinitely — inflating results vs live EA behaviour.
            # CHANGED: April 2026 — hard close hour (MT5/backtester parity)
            if hard_close_hour >= 0:
                try:
                    _candle_hour = pd.Timestamp(future_candle['timestamp']).hour
                    if _candle_hour == hard_close_hour:
                        exit_price  = float(future_candle["open"])
                        exit_time   = future_candle["timestamp"]
                        exit_reason = "HARD_CLOSE_HOUR"
                        occupied_until_idx = future_idx
                        break
                except Exception:
                    pass

            result = exit_strategy.on_new_candle(candle_dict, pos)
            if result:
                exit_price  = result["exit_price"]
                exit_time   = future_candle["timestamp"]
                exit_reason = result["reason"]
                occupied_until_idx = future_idx
                break

        if exit_price is None:
            last_candle = df.iloc[-1]
            exit_price  = float(last_candle["close"])
            exit_time   = last_candle["timestamp"]
            exit_reason = "END_OF_DATA"
            # WHY (Phase A.28.2): Old code set occupied_until_idx to the
            #      very last index of the dataset on END_OF_DATA, which
            #      then made the next-iteration check
            #      `if sig_idx <= occupied_until_idx: continue` skip
            #      every remaining signal. One trade that drifted to the
            #      end killed the entire combo. Use the actual position
            #      where the trade was finally booked instead — which
            #      for run_backtest's per-candle simulation is the
            #      future_idx the loop landed on, or the dataset end
            #      only if we genuinely reached it. The variable
            #      future_idx is set inside the loop when an exit fires;
            #      when no exit fires we fall through to here. The
            #      cleanest sentinel is the signal index itself (the
            #      candle where this trade opened) — subsequent signals
            #      strictly greater than sig_idx get a fair chance to
            #      open their own trades.
            # CHANGED: April 2026 — Phase A.28.2
            occupied_until_idx = sig_idx

        # Track exit position for cooldown (integer position of last closed trade)
        _last_exit_pos = index_positions.get(occupied_until_idx, _last_exit_pos)

        pnl_pips = (exit_price - entry_price) / pip_size
        if trade_dir == "SELL":
            pnl_pips = -pnl_pips

        # WHY: Spread is now baked into entry_price (added above), so pnl_pips
        #      is already net of spread — subtracting it again would double-count.
        #      Only subtract commission here. Matches fast_backtest (line ~826)
        #      which also only subtracts commission_pips after spread-adjusted entry.
        # CHANGED: April 2026 — remove spread double-count (MT5 parity fix)
        cost     = commission_pips
        net_pips = pnl_pips - cost

        # WHY: Asymmetric, sign-preserving swap. See _select_swap_pips.
        # CHANGED: April 2026 — asymmetric swap
        swap_per_night_rb = _select_swap_pips(
            trade_dir, swap_long_pips_per_night, swap_short_pips_per_night)
        swap_nights    = 0
        swap_cost_pips = 0.0
        if swap_per_night_rb != 0:
            entry_dt    = pd.to_datetime(entry_time)
            exit_dt     = pd.to_datetime(exit_time)
            swap_nights = _count_swap_nights(entry_dt, exit_dt)
            if swap_nights > 0:
                # swap_per_night_rb is already in pips/night — signed.
                swap_cost_pips = swap_nights * swap_per_night_rb
                net_pips += swap_cost_pips    # signed: negative = cost

        # Position sizing and dollar P&L (optional, when account_size is provided)
        if account_size is not None:
            # WHY (T1b): ATR exits have no sl_pips attribute — old fallback used
            #      default_sl_pips=150, but the ATR SL is often 2000+ pips on XAUUSD.
            #      Lots sized for 150 and stopped at 3000 = 20× intended risk per
            #      losing trade → DD breach in one trade. Use the expected SL
            #      distance for the specific exit instance.
            # CHANGED: April 2026 — T1b SL-aware lot sizing
            # Build the entry_candle dict the helper needs. entry_price is already
            # computed above this block; the entry candle is available either as
            # `next_candle` (fast_backtest) or reconstructable from ind.loc at
            # entry_pos_int. We need the indicator columns to read ATR.
            try:
                _entry_for_sizing = {}
                if 0 <= entry_pos_int + 1 < len(ind):
                    _ind_idx_entry = ind.index[entry_pos_int + 1]
                    _entry_for_sizing = dict(ind.loc[_ind_idx_entry])
            except Exception:
                _entry_for_sizing = {}
            _sl_for_sizing = _expected_sl_pips_for_exit(
                exit_strategy, _entry_for_sizing, pip_size, default_sl_pips
            )

            # WHY: When compound_equity=True, use running balance for sizing
            #      so winners compound. Otherwise use static account_size.
            # CHANGED: April 2026 — equity-tracking lot sizing
            _sizing_equity = _running_balance if (compound_equity and _running_balance) else account_size
            risk_dollars = _sizing_equity * (risk_per_trade_pct / 100.0)
            lot_size = risk_dollars / (_sl_for_sizing * pip_value_per_lot)
            # WHY: Silent min(lot_size, 100.0) hid absurdly large positions
            #      (e.g. 500-lot size on a $10M virtual account) and made stats
            #      look better than they would be on a real broker.
            # CHANGED: April 2026 — warn instead of silently capping
            if lot_size > 100.0:
                log.warning(f"  [WARN] Computed lot size {lot_size:.1f} exceeds 100 — "
                            f"check account_size / risk_pct / sl_pips settings")
            lot_size = max(0.01, lot_size)

            # WHY (T1b): Make SL-aware sizing visible in the backtest log so the user
            #      can verify ATR exits are getting large _sl_for_sizing values.
            # CHANGED: April 2026 — T1b sizing diagnostics
            _exit_name = type(exit_strategy).__name__
            if not hasattr(exit_strategy, '_t1b_sizing_logged'):
                exit_strategy._t1b_sizing_logged = 0
            if exit_strategy._t1b_sizing_logged < 3:
                log.info(
                    f"  [T1b] {_exit_name} trade {len(trades)+1}: "
                    f"sl_for_sizing={_sl_for_sizing:.1f} pips → "
                    f"risk ${risk_dollars:.2f} → lot {lot_size:.3f}"
                )
                exit_strategy._t1b_sizing_logged += 1
            # WHY (leverage): Same margin cap as vectorized path — see comment
            #      above. Uses entry_price from this trade's fill.
            # CHANGED: April 2026 — margin-aware lot sizing
            if leverage > 0 and entry_price > 0:
                _margin_per_lot = (contract_size * entry_price) / leverage
                _max_lots = (_sizing_equity * 0.95) / _margin_per_lot
                if lot_size > _max_lots:
                    lot_size = max(0.01, round(_max_lots, 2))
            dollar_pnl = round(net_pips * pip_value_per_lot * lot_size, 2)

            # WHY: Update running balance after each trade so compounding
            #      works for the next trade. Floor at 50% of starting capital
            #      to prevent degenerate lot sizes after heavy drawdowns.
            # CHANGED: April 2026 — equity-tracking lot sizing
            if compound_equity and _running_balance is not None and dollar_pnl is not None:
                _running_balance = max(account_size * 0.5, _running_balance + dollar_pnl)
        else:
            lot_size   = None
            dollar_pnl = None

        # WHY: fast_backtest exports trade['pips'] as post-spread gross
        #      (because spread is baked into entry_price before the pnl
        #      calc). run_backtest previously only exported pnl_pips (gross
        #      pre-spread), cost_pips, and net_pips — downstream code
        #      doing trade.get('pips') silently got None from run_backtest
        #      and a post-spread value from fast_backtest. Add a matching
        #      'pips' key here so both backtester outputs share semantics.
        #      pips = pnl_pips (spread already in entry_price, so pnl_pips is
        #      already post-spread); net_pips = pips - commission.
        #      Old formula subtracted spread_pips here which double-counted it
        #      (once in entry_price above, once here). Now matches fast_backtest.
        # CHANGED: April 2026 — Phase 28 Fix 3 — add 'pips' key for schema
        #          consistency with fast_backtest; spread already in entry_price
        _pips_post_spread = pnl_pips
        # WHY (Phase A.42): Increment daily counter after trade opens.
        # CHANGED: April 2026 — Phase A.42
        if _a42_limit_rb > 0:
            try:
                _a42_entry_day_rb = str(pd.Timestamp(entry_time).date())
                _a42_daily_counts_rb[_a42_entry_day_rb] = _a42_daily_counts_rb.get(_a42_entry_day_rb, 0) + 1
            except Exception:
                pass
        trades.append({
            "entry_time":  entry_time,
            "exit_time":   exit_time,
            "direction":   trade_dir,
            # WHY: round(,2) truncates forex prices (5 decimal places).
            # CHANGED: April 2026 — use 5 decimal places like vectorized path
            "entry_price": round(entry_price, 5),
            "exit_price":  round(exit_price, 5),
            "pips":        round(_pips_post_spread, 1),
            "pnl_pips":    round(pnl_pips, 1),
            # WHY: cost_pips is an informational field showing the total
            #      transaction cost (spread + commission). Spread is baked into
            #      entry_price and commission is subtracted from net_pips, so
            #      the sum is the full round-trip cost — matches fast_backtest
            #      which also reports spread + commission regardless of how
            #      each component is applied in the P&L calculation.
            # CHANGED: April 2026 — consistent cost_pips with fast_backtest
            "cost_pips":   round(_trade_spread + commission_pips, 1),
            "net_pips":    round(net_pips, 1),
            "exit_reason":  exit_reason,
            "candles_held": candles_held,
            "rule_id":      rule_id,
            "lot_size":     lot_size,
            "dollar_pnl":   dollar_pnl,
            "swap_nights":  swap_nights,
            # WHY: Dropped legacy 'swap_cost_pips' key — use 'cost_swap_pips'
            #      which is consistent with fast_backtest and the cost breakdown.
            # CHANGED: April 2026 — unify swap key name
            # WHY: Per-trade cost breakdown for diagnostic summary.
            # CHANGED: April 2026 — cost breakdown
            "cost_spread_pips":     round(-float(_trade_spread), 1),
            "cost_commission_pips": round(-float(commission_pips), 1),
            "cost_swap_pips":       round(float(swap_cost_pips), 1),
        })

    return trades


def fast_backtest(df, ind, rules, exit_strategy,
                  direction="BUY", pip_size=0.01,
                  spread_pips=2.5, commission_pips=0.0,
                  slippage_pips=0.0,
                  account_size=None, risk_per_trade_pct=1.0,
                  default_sl_pips=150.0, pip_value_per_lot=1.0,
                  # WHY (Phase A.42): 0 = no limit; positive int = max trades
                  #      per calendar day, matching live EA's MaxTradesPerDay.
                  # CHANGED: April 2026 — Phase A.42
                  max_trades_per_day=0,
                  # WHY (leverage): 0 = no margin check (backward compat).
                  # CHANGED: April 2026 — margin-aware lot sizing
                  leverage=0, contract_size=100.0,
                  # WHY: When True, lot sizing uses running balance so
                  #      winners compound into larger subsequent positions.
                  # CHANGED: April 2026 — equity-tracking lot sizing
                  compound_equity=False,
                  # WHY: hard_close_hour / cooldown_candles — same semantics as
                  #      run_backtest. Defaults maintain backward compatibility.
                  # CHANGED: April 2026 — hard close + cooldown (MT5 parity)
                  hard_close_hour=-1,
                  cooldown_candles=0,
                  # WHY: variable_spread / max_spread_pips — see run_backtest.
                  # CHANGED: April 2026 — session-based variable spread model
                  variable_spread=False,
                  max_spread_pips=0,
                  # WHY: data_dir for tick-aware exit ambiguity resolution.
                  # CHANGED: April 2026 — tick data for exit ambiguity resolution
                  data_dir=None,
                  # WHY: Asymmetric swap — see _select_swap_pips.
                  # CHANGED: April 2026 — asymmetric swap
                  swap_long_pips_per_night=0.0,
                  swap_short_pips_per_night=0.0,
                  # WHY: Seed the slippage RNG so fast_backtest agrees with
                  #      run_backtest on the same seed. None = unseeded.
                  # CHANGED: April 2026 — slippage symmetry fix
                  slippage_seed=None):
    """
    Fast backtest — NO DataFrame copies, NO SMART recomputation.

    WHY: run_backtest copies candles_df (130K rows) and indicators_df (670 cols)
         on EVERY call. The deep optimizer calls it 275 times = ~385 GB of copies
         for data that never changes. This function takes pre-prepared DataFrames
         and only builds the boolean mask + simulates trades.

    IMPORTANT: df and ind must be:
      - Already trimmed (warmup removed)
      - Already have SMART/REGIME features if needed
      - Same length and aligned by index
      - NOT modified by this function (read-only access)

    CHANGED: April 2026 — 10-50x speedup for deep optimizer
    """
    trades = []
    # WHY: Running balance for compound equity — same as run_backtest.
    # CHANGED: April 2026 — equity-tracking lot sizing
    _running_balance = float(account_size) if account_size is not None else None
    _skipped_count = 0   # FIX 12E: track SANE_PIP_LIMIT skips

    if len(df) == 0:
        return trades

    # ── VECTORIZED: build entry signal mask ──────────────────────────────
    # WHY: This is the only part that changes between iterations —
    #      different threshold values produce different masks.
    #      Everything else (indicator values, candle data) is identical.
    signal_mask     = pd.Series(False, index=ind.index)
    signal_rule_ids = pd.Series(-1,    index=ind.index, dtype=int)

    # WHY (Phase A.24): same numpy-based mask building as run_backtest
    #      to avoid _indexed_same crashes. See run_backtest WHY block
    #      for full rationale — applies identically here.
    # CHANGED: April 2026 — Phase A.24
    _ind_n = len(ind)

    # Pre-flight: detect duplicate column names in the indicators frame
    _dup_cols = ind.columns[ind.columns.duplicated()].tolist()
    if _dup_cols:
        log.warning(
            f"  [fast_backtest] indicators frame has {len(_dup_cols)} duplicate "
            f"column names: {_dup_cols[:10]}{'...' if len(_dup_cols) > 10 else ''}. "
            f"This is the most likely cause of past _indexed_same crashes. "
            f"Each duplicate column will be collapsed to its first occurrence."
        )
        # De-duplicate by taking the first occurrence of each name
        ind = ind.loc[:, ~ind.columns.duplicated()]

    for rule_idx, rule in enumerate(rules):
        # All rules normalized to WIN in load_rules_from_report
        if rule.get('prediction') != 'WIN':
            continue
        rule_mask_np = np.ones(_ind_n, dtype=bool)
        valid_rule   = True

        for cond in rule.get("conditions", []):
            col = cond.get("feature", "")
            if col not in ind.columns:
                valid_rule = False
                break

            # Extract the column as a numpy float array.
            # If ind[col] returned a DataFrame for any reason (which
            # shouldn't happen after de-dup above but is defensive),
            # take the first sub-column.
            _raw = ind[col]
            if isinstance(_raw, pd.DataFrame):
                log.warning(
                    f"  [fast_backtest] ind[{col!r}] returned a DataFrame "
                    f"with shape {_raw.shape}; taking first column."
                )
                _raw = _raw.iloc[:, 0]

            try:
                col_arr = pd.to_numeric(_raw, errors='coerce').to_numpy(dtype=float, copy=False)
            except Exception as _coerce_err:
                log.warning(
                    f"  [fast_backtest] could not coerce column {col!r} to numeric "
                    f"({type(_raw).__name__}, dtype={getattr(_raw, 'dtype', '?')}): "
                    f"{_coerce_err!r} — rule skipped."
                )
                valid_rule = False
                break

            try:
                _val_f = float(cond.get("value", 0))
            except Exception:
                log.warning(
                    f"  [fast_backtest] rule {rule_idx} has non-numeric value "
                    f"{cond.get('value')!r} on feature {col!r} — rule skipped."
                )
                valid_rule = False
                break

            op = cond.get("operator", ">")
            # numpy comparisons of float arrays vs scalar ALWAYS return
            # bool arrays — they cannot return NotImplemented.
            with np.errstate(invalid='ignore'):
                if op == "<=":
                    cond_arr = col_arr <= _val_f
                elif op == ">":
                    cond_arr = col_arr >  _val_f
                elif op == "<":
                    cond_arr = col_arr <  _val_f
                elif op == ">=":
                    cond_arr = col_arr >= _val_f
                elif op == "==":
                    cond_arr = col_arr == _val_f
                elif op == "!=":
                    cond_arr = col_arr != _val_f
                else:
                    log.warning(
                        f"  [fast_backtest] Unknown operator {op!r} on feature "
                        f"{col!r} — rule skipped. Supported: <=, >, <, >=, ==, !="
                    )
                    valid_rule = False
                    break

            # NaN/inf values become False — no signal if indicator is invalid
            cond_arr = np.where(np.isnan(col_arr) | np.isinf(col_arr), False, cond_arr)
            rule_mask_np &= cond_arr

        if not valid_rule:
            continue

        # Convert numpy mask back to Series for downstream code
        rule_mask = pd.Series(rule_mask_np, index=ind.index)

        new_signals = rule_mask & ~signal_mask
        signal_mask |= rule_mask
        signal_rule_ids[new_signals] = rule_idx

    # ── Phase A.38a / A.43: Regime filter gating ───────────────────────
    # WHY (Phase A.38a): Same gate as run_backtest. fast_backtest is the
    #      hot path used by the comparison matrix and deep optimizer —
    #      called hundreds of times per scenario. log_filter_summary_once
    #      deduplicates log spam: one summary per distinct filter config
    #      per process, not per call.
    # WHY (Phase A.43): Use per-rule baked conditions when available.
    # CHANGED: April 2026 — Phase A.38a / A.43
    try:
        from project2_backtesting.regime_filter_runtime import (
            build_regime_pass_mask, log_filter_summary_once,
        )
        _a43_rule_rf = None
        for _r in rules:
            _rf = _r.get('regime_filter')
            if _rf and isinstance(_rf, list) and len(_rf) > 0:
                _a43_rule_rf = _rf
                break
        _a43_has_key = any('regime_filter' in _r for _r in rules)
        if _a43_has_key and _a43_rule_rf is None:
            _a43_override = []
        else:
            _a43_override = _a43_rule_rf
        _a38a_regime_mask, _a38a_info = build_regime_pass_mask(
            ind, rule_action=direction, override_conditions=_a43_override,
        )
        if _a38a_info.get('enabled'):
            log_filter_summary_once(_a38a_info, source_label='fast_backtest')
            _fb_pre  = int(signal_mask.sum())
            signal_mask = signal_mask & pd.Series(_a38a_regime_mask, index=ind.index)
            _fb_post = int(signal_mask.sum())
            # WHY (Phase A.38b): Store pre/post counts on the function
            #      object so run_comparison_matrix can read them without
            #      changing fast_backtest's return signature. The caller
            #      is synchronous so there's no race.
            # CHANGED: April 2026 — Phase A.38b
            fast_backtest._last_sig_before = _fb_pre
            fast_backtest._last_sig_after  = _fb_post
    except Exception as _a38a_e:
        log.warning(
            f"[A.38a/fast_backtest] regime filter failed — proceeding without it: "
            f"{type(_a38a_e).__name__}: {_a38a_e}"
        )

    signal_indices = df.index[signal_mask].tolist()

    if not signal_indices:
        return trades

    # ── Use vectorized exit for FixedSLTP ────────────────────────────────
    # WHY: Same optimization as run_backtest — vectorized exit detection.
    #      ATRFixedSLTP has per-trade SL/TP (set in on_entry from ATR)
    #      so it must NOT use the vectorized path which assumes constant SL/TP.
    # CHANGED: April 2026 — vectorized FixedSLTP in fast_backtest
    # CHANGED: April 2026 — exclude ATRFixedSLTP from vectorized path
    from project2_backtesting.exit_strategies import FixedSLTP, ATRFixedSLTP
    if isinstance(exit_strategy, FixedSLTP) and not isinstance(exit_strategy, ATRFixedSLTP):
        return _vectorized_fixed_sltp_exits(
            df, signal_indices, signal_rule_ids, rules,
            exit_strategy, direction, pip_size,
            spread_pips, commission_pips, slippage_pips,
            account_size, risk_per_trade_pct,
            default_sl_pips, pip_value_per_lot,
            swap_long_pips_per_night=swap_long_pips_per_night,
            swap_short_pips_per_night=swap_short_pips_per_night,
            max_trades_per_day=max_trades_per_day,
            leverage=leverage, contract_size=contract_size,
            compound_equity=compound_equity,
            variable_spread=variable_spread,
            max_spread_pips=max_spread_pips,
            slippage_seed=slippage_seed,
        )

    # ── Simulate trades from signal candles ──────────────────────────────
    occupied_until_idx = -1
    _last_exit_pos_fbt = -1   # integer position of last exit (for cooldown)
    index_positions = {idx: pos for pos, idx in enumerate(df.index)}

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue
        # WHY: Cooldown prevents back-to-back entries — mirrors EA's CooldownMinutes.
        # CHANGED: April 2026 — cooldown between trades (MT5 parity)
        if cooldown_candles > 0 and _last_exit_pos_fbt >= 0:
            _sig_pos_fbt = index_positions.get(sig_idx, 0)
            if _sig_pos_fbt <= _last_exit_pos_fbt + cooldown_candles:
                continue

        entry_pos_int = index_positions.get(sig_idx, 0)
        if entry_pos_int + 1 >= len(df):
            continue
        next_candle = df.iloc[entry_pos_int + 1]

        entry_time  = next_candle['timestamp']
        entry_price = float(next_candle['open'])

        # WHY: Old code added spread only to BUY entries. SELL entries
        #      receive the bid (open - spread/2), so spread should also
        #      cost the SELL trader — entry_price should be SUBTRACTED
        #      by spread_pips (making the SELL entry worse). Without
        #      this fix, SELL strategies look ~2 pips better than live.
        # CHANGED: April 2026 — fix SELL spread cost (audit Family #4)
        if direction == "BUY":
            entry_price += (spread_pips + slippage_pips) * pip_size
        else:
            entry_price -= (spread_pips + slippage_pips) * pip_size

        # Simulate trade exit by stepping through future candles
        # WHY: Exit strategies implement on_new_candle(candle, pos) which is
        #      called per-candle and returns None until an exit triggers.
        #      They DON'T have a single check_exit() method.
        # CHANGED: April 2026 — match actual exit strategy interface
        future_candles = df.iloc[entry_pos_int + 1:]

        # WHY: Exit strategies (TimeBased, ATRBased, etc.) read candles_held and
        #      current_pnl_pips to decide when to exit. Without these fields,
        #      time-based exits silently KeyError → are caught → never fire →
        #      trades run to END_OF_DATA → astronomical fake pip wins.
        # CHANGED: April 2026 — fix missing candles_held / minutes_held
        # WHY (Phase 28 Fix 4): highest_since_entry / lowest_since_entry were
        #      seeded from df.iloc[entry_pos_int] — the SIGNAL candle, one bar
        #      BEFORE the entry. Trailing stops and ATR-based exits then
        #      referenced a candle that did not exist when the trade opened.
        #      Seed from next_candle (the actual entry candle, already
        #      fetched above at entry_pos_int + 1). Also update entry_candle
        #      to match. Matches run_backtest which seeds from next_candle.
        # CHANGED: April 2026 — Phase 28 Fix 4 — seed trackers from entry
        #          candle (audit Part C #21)
        pos_info = {
            'entry_price':      entry_price,
            'direction':        direction,
            'entry_time':       entry_time,
            'entry_candle':     next_candle,
            'candles_held':     0,    # incremented per candle below
            'minutes_held':     0,    # incremented per candle below
            'current_pnl_pips': 0,    # updated per candle below
            'highest_since_entry': float(next_candle['high']),
            'lowest_since_entry':  float(next_candle['low']),
        }

        # Some exits (ATRBased) need on_entry hook for setup
        # WHY (Code Audit Fix — Bug 1a): Old code passed only df.iloc
        #      (price data) to on_entry. Exit strategies like ATRBased
        #      need indicator data (H1_atr_14) which lives in `ind`.
        #      Without the merge, ATR/IndicatorExit never find their
        #      columns and produce garbage results. Match run_backtest's
        #      behavior: merge price + indicator data into a single dict.
        # CHANGED: April 2026 — Code Audit Fix
        if hasattr(exit_strategy, 'on_entry'):
            try:
                _entry_dict = df.iloc[entry_pos_int].to_dict()
                if 0 <= entry_pos_int < len(ind.index):
                    _sig_idx = ind.index[entry_pos_int]
                    _entry_dict.update(ind.loc[_sig_idx].to_dict())
                exit_strategy.on_entry(_entry_dict)
            except Exception:
                pass

        # Infer candle duration once per trade (for minutes_held)
        # WHY: Using only the first two candles can pick a gap (e.g. session
        #      open after weekend) and give a wildly wrong duration. Median of
        #      up to 10 consecutive gaps is robust against isolated outliers.
        # CHANGED: April 2026 — median-gap inference
        candle_minutes = 60
        if len(future_candles) >= 2:
            try:
                _sample = future_candles.iloc[:min(11, len(future_candles))]
                _ts     = pd.to_datetime(_sample['timestamp'])
                _gaps   = [
                    max(1, int((_ts.iloc[i+1] - _ts.iloc[i]).total_seconds() / 60))
                    for i in range(len(_ts) - 1)
                ]
                if _gaps:
                    candle_minutes = int(np.median(_gaps))
            except Exception:
                pass

        # WHY: Provide tick and M1 loaders. Fallback: ticks → M1 → conservative.
        # CHANGED: April 2026 — tick + M1 fallback in position info (fast_backtest)
        if data_dir and _check_ticks_available(data_dir):
            _d, _cm = data_dir, candle_minutes
            pos_info['_tick_loader'] = lambda ts, _d=_d, _cm=_cm: _load_ticks_for_candle(_d, ts, _cm)
        else:
            pos_info['_tick_loader'] = None
        if data_dir:
            _d, _cm = data_dir, candle_minutes
            pos_info['_m1_loader'] = lambda ts, _d=_d, _cm=_cm: _load_m1_for_candle(_d, ts, _cm)
        else:
            pos_info['_m1_loader'] = None

        # WHY (Phase A.10): Old code did `candle = future_candles.iloc[ci]`
        #      then `float(candle['close'])` etc. on every iteration. Each
        #      `.iloc[ci]` row read is ~10-50µs in pandas, and each
        #      `float(candle['key'])` does a Series lookup + conversion.
        #      With ~1000 trades × ~100-500 candles each = 100K-500K
        #      iterations, this dominated backtest runtime.
        #      Optimization: pre-extract close/high/low as numpy arrays
        #      ONCE before the loop, then read them by integer position.
        #      The exit_strategy.on_new_candle() callback still receives
        #      a pd.Series via .iloc[ci] because exit strategies access
        #      fields by name — that single retained .iloc is the only
        #      pandas access remaining in the hot path.
        # CHANGED: April 2026 — Phase A.10 — numpy array hot loop
        _closes_np = future_candles['close'].to_numpy(dtype=float, copy=False)
        _highs_np  = future_candles['high'].to_numpy(dtype=float, copy=False)
        _lows_np   = future_candles['low'].to_numpy(dtype=float, copy=False)
        _n_future  = len(_closes_np)

        result = None
        exit_idx = -1
        for ci in range(1, _n_future):
            # WHY (same-bar exit bias fix): The loop previously started at
            #      ci=0, which is future_candles.iloc[0] — the ENTRY candle
            #      itself. pos_info['highest_since_entry'] is seeded from that
            #      same candle's HIGH before the loop, so ci=0 immediately
            #      triggered trailing-stop exits on the entry bar: look-ahead
            #      bias. Starting at ci=1 skips the entry candle. With ci now
            #      1-based, candles_held = ci directly (ci=1 → held 1 candle).
            # CHANGED: April 2026 — same-bar exit look-ahead bias fix
            pos_info['candles_held'] = ci
            pos_info['minutes_held'] = ci * candle_minutes
            close = _closes_np[ci]
            high  = _highs_np[ci]
            low   = _lows_np[ci]
            pos_info['current_pnl_pips'] = (
                (close - entry_price) / pip_size if direction == "BUY"
                else (entry_price - close) / pip_size
            )
            if high > pos_info['highest_since_entry']:
                pos_info['highest_since_entry'] = high
            if low < pos_info['lowest_since_entry']:
                pos_info['lowest_since_entry'] = low

            # WHY (Phase A.10): exit strategies access candle fields by
            #      name (candle['close'], candle['high'], etc.) so we
            #      still need a Series-shaped object for the callback.
            #      This is the only retained .iloc in the hot loop.
            # WHY (Code Audit Fix — Bug 1b): Old code passed only price
            #      data from future_candles. Exit strategies that read
            #      indicator columns (ATRBased reads H1_atr_14,
            #      IndicatorExit reads H1_rsi_14) got None and silently
            #      degraded. Merge indicator row from `ind` into the
            #      candle dict, matching run_backtest's behavior.
            #      Performance note: .to_dict() + .update() adds ~20µs
            #      per candle. The vectorized FixedSLTP path (majority
            #      of combos) is unaffected.
            # CHANGED: April 2026 — Code Audit Fix
            candle = future_candles.iloc[ci]
            _future_abs_idx = entry_pos_int + 1 + ci
            if _future_abs_idx < len(ind):
                try:
                    _ind_idx = ind.index[_future_abs_idx]
                    _candle_dict = candle.to_dict()
                    _candle_dict.update(ind.loc[_ind_idx].to_dict())
                    candle = _candle_dict
                except Exception:
                    pass

            # WHY: Hard close overrides SL/TP — force-exit at the specified GMT hour.
            #      Checked before the exit strategy so it always takes priority.
            # CHANGED: April 2026 — hard close hour (MT5/backtester parity)
            if hard_close_hour >= 0:
                try:
                    _hc_ts = (candle['timestamp'] if isinstance(candle, dict)
                              else candle.get('timestamp', ''))
                    if pd.Timestamp(_hc_ts).hour == hard_close_hour:
                        _open_val = (candle.get('open', float(future_candles.iloc[ci]['open']))
                                     if isinstance(candle, dict)
                                     else float(future_candles.iloc[ci]['open']))
                        step_result = {'exit_price': _open_val, 'reason': 'HARD_CLOSE_HOUR'}
                        result   = step_result
                        exit_idx = ci
                        break
                except Exception:
                    pass

            try:
                step_result = exit_strategy.on_new_candle(candle, pos_info)
            except Exception as e:
                # WHY (Phase 35 Fix 5): Old code logged only on ci==0.
                #      Exit strategies that crashed on every call had
                #      iterations 1..N silently return None, the trade
                #      ran to END_OF_DATA, hit SANE_PIP_LIMIT, got
                #      silently dropped. User saw reduced trade count
                #      with no log. Track unique exception messages
                #      per trade (dedupe) so every distinct error
                #      surfaces exactly once. Escalate to warning.
                # CHANGED: April 2026 — Phase 35 Fix 5 — dedupe exit errors
                #          (audit Part C MED #23)
                _err_key = f"{type(e).__name__}:{str(e)[:100]}"
                if not hasattr(exit_strategy, '_seen_errors'):
                    exit_strategy._seen_errors = set()
                if _err_key not in exit_strategy._seen_errors:
                    exit_strategy._seen_errors.add(_err_key)
                    log.warning(
                        f"  [fast_backtest exit error] "
                        f"{type(exit_strategy).__name__}.on_new_candle: "
                        f"{type(e).__name__}: {e}"
                    )
                step_result = None

            if step_result is not None:
                result = step_result
                exit_idx = ci
                break

        # If no exit triggered, close at last candle
        if result is None:
            if len(future_candles) == 0:
                continue
            last_candle = future_candles.iloc[-1]
            result = {
                'exit_price': float(last_candle['close']),
                'reason':     'END_OF_DATA',
            }
            exit_idx = len(future_candles) - 1
            # WHY (Phase A.28.2): END_OF_DATA in the iterative path used
            #      to set occupied_until_idx = df.index[-1] further down,
            #      blocking every subsequent signal forever. The fix
            #      lives at the assignment site below; this comment is
            #      a marker so future readers understand why that
            #      line uses the actual exit position instead of the
            #      dataset end.
            # CHANGED: April 2026 — Phase A.28.2

        exit_price  = result['exit_price']
        exit_reason = result.get('reason', result.get('exit_reason', 'unknown'))

        exit_candle = future_candles.iloc[exit_idx]
        exit_time   = exit_candle['timestamp']

        if direction == "BUY":
            pips = (exit_price - entry_price) / pip_size
        else:
            pips = (entry_price - exit_price) / pip_size

        # WHY: Sanity check — if pips is absurdly large the exit strategy
        #      silently failed and the trade ran to END_OF_DATA years later.
        #      Skip rather than poison the stats with fake results.
        # CHANGED: April 2026 — pip sanity check
        # WHY (Phase 35 Fix 1): Old limit of 50,000 pips silently dropped
        #      legitimate long-hold XAUUSD trades. A BUY from $1800 (2020)
        #      to $2500 (2024) = 70K pips of raw movement, which is a
        #      real trade worth ~$7000/lot on XAUUSD, not a silent exit
        #      failure. Raise the catastrophic-skip limit to 200K
        #      (covers any realistic multi-year hold), and add an INFO
        #      log for trades in the [50K, 200K] range so we can still
        #      see them in logs without dropping them.
        # CHANGED: April 2026 — Phase 35 Fix 1 — tiered pip sanity check
        #          (audit Part C MED #18)
        SANE_PIP_LIMIT_SKIP  = 200_000   # catastrophic — drop silently
        SANE_PIP_LIMIT_LARGE = 50_000    # large but plausible — keep + log
        if abs(pips) > SANE_PIP_LIMIT_SKIP:
            _skipped_count += 1
            if _skipped_count <= 5:   # log first few occurrences
                log.warning(f"  [SKIP] Absurd pips: {pips:.0f} "
                            f"(entry={entry_price:.2f}, exit={exit_price:.2f}, "
                            f"reason={exit_reason}) — "
                            f"exceeds {SANE_PIP_LIMIT_SKIP} pip catastrophic "
                            f"limit; likely silent exit failure")
            continue
        if abs(pips) > SANE_PIP_LIMIT_LARGE:
            # INFO only — trade is kept, just flagged for attention
            log.info(f"  [LARGE] Large pip trade kept: {pips:.0f} "
                     f"(entry={entry_price:.2f}, exit={exit_price:.2f}, "
                     f"reason={exit_reason}) — legitimate long hold, "
                     f"above {SANE_PIP_LIMIT_LARGE}-pip log threshold")

        net_pips = pips - commission_pips

        # WHY: Same as Fix 7B — prefer actual exit_strategy.sl_pips over
        #      the default. See Fix 7B comment for full explanation.
        # CHANGED: April 2026 — use actual sl_pips (audit Family #2)
        _actual_sl = getattr(exit_strategy, 'sl_pips', None)
        _sl_for_sizing = float(_actual_sl) if _actual_sl else float(default_sl_pips)

        lot_size = 0.01
        if account_size and risk_per_trade_pct > 0:
            # WHY: When compound_equity=True, use running balance for sizing.
            # CHANGED: April 2026 — equity-tracking lot sizing
            _sizing_equity = _running_balance if (compound_equity and _running_balance) else account_size
            risk_dollars = _sizing_equity * (risk_per_trade_pct / 100)
            lot_size = risk_dollars / (_sl_for_sizing * pip_value_per_lot) if _sl_for_sizing > 0 else 0.01
            lot_size = max(0.01, round(lot_size, 2))
            # WHY (leverage): Same margin cap as run_backtest.
            # CHANGED: April 2026 — margin-aware lot sizing
            if leverage > 0 and entry_price > 0:
                _margin_per_lot = (contract_size * entry_price) / leverage
                _max_lots = (_sizing_equity * 0.95) / _margin_per_lot
                if lot_size > _max_lots:
                    lot_size = max(0.01, round(_max_lots, 2))

        net_profit = net_pips * pip_value_per_lot * lot_size

        # WHY: Update running balance for compound equity.
        # CHANGED: April 2026 — equity-tracking lot sizing
        if compound_equity and _running_balance is not None and account_size:
            _running_balance = max(account_size * 0.5, _running_balance + net_profit)

        # WHY (Quick Fix + same-bar bias fix): The vectorized path includes
        #      candles_held and cost_pips in each trade dict. The non-vectorized
        #      path was missing both. After the same-bar bias fix, the loop
        #      starts at ci=1, so exit_idx is 1-based (minimum 1). Therefore
        #      candles_held = exit_idx (not exit_idx + 1).
        # CHANGED: April 2026 — add candles_held + cost_pips; updated for bias fix
        trade = {
            'entry_time':   str(entry_time),
            'exit_time':    str(exit_time),
            'entry_price':  round(entry_price, 5),
            'exit_price':   round(exit_price, 5),
            'direction':    direction,
            'pips':         round(pips, 1),
            'net_pips':     round(net_pips, 1),
            'cost_pips':    round(spread_pips + commission_pips, 1),
            'net_profit':   round(net_profit, 2),
            'lot_size':     lot_size,
            'candles_held': exit_idx,
            'exit_reason':  exit_reason,
            'rule_id':      int(signal_rule_ids.loc[sig_idx]),
        }
        trades.append(trade)

        # Mark occupied candles and update cooldown tracker
        occupied_until_idx = df.index[min(entry_pos_int + 1 + exit_idx, len(df) - 1)]
        _last_exit_pos_fbt = min(entry_pos_int + 1 + exit_idx, len(df) - 1)

    if _skipped_count > 0:
        # CHANGED: April 2026 — Phase 35 Fix 1b — updated limit reference
        log.warning(f"  [fast_backtest] Skipped {_skipped_count} trade(s) with absurd pips "
                    f"(SANE_PIP_LIMIT_SKIP=200_000). Check exit strategy for silent failures.")

    return trades


def compute_stats(trades):
    """Compute gross and net performance statistics."""
    if not trades:
        return {
            "total_trades": 0, "win_rate": 0,
            "avg_pips": 0, "net_avg_pips": 0,
            "total_pips": 0, "net_total_pips": 0,
            "profit_factor": 0, "net_profit_factor": 0,
            "max_dd_pips": 0, "total_costs": 0,
            "avg_winner": 0, "avg_loser": 0,
            "best_trade": 0, "worst_trade": 0,
            "expectancy": 0, "risk_reward_ratio": 0,
            "std_pips": 0, "sharpe_ish": 0,
            "max_win_streak": 0, "max_loss_streak": 0,
            "trades_per_day": 0, "days_per_trade": 0,
            "recovery_factor": 0, "winners": 0, "losers": 0, "breakeven": 0,
            # WHY (T2b): zero defaults for the three time-distribution fields.
            # CHANGED: April 2026 — T2b
            "active_month_coverage": 0.0,
            "longest_dormant_days":  0.0,
            "total_calendar_days":   0,
        }

    # WHY: Vectorized backtest writes 'pips', non-vectorized writes 'pnl_pips'.
    #      Accept either to avoid KeyError.
    # CHANGED: April 2026 — accept both key names
    def _gross(t):
        return t.get("pnl_pips", t.get("pips", 0))

    gross  = [_gross(t) for t in trades]
    net    = [t.get("net_pips", _gross(t)) for t in trades]
    costs  = sum(t.get("cost_pips", 0) for t in trades)

    # WHY: Including break-even (p==0) in net_losers inflated loser count and
    #      deflated avg_l, making the strategy look worse than it is.
    #      Expectancy formula (wr*avg_w + (1-wr)*avg_l) also misallocated
    #      break-evens; np.mean(net) is exact and needs no decomposition.
    # CHANGED: April 2026 — separate break-evens; direct expectancy
    net_winners  = [p for p in net if p > 0]
    net_losers   = [p for p in net if p < 0]
    net_breakeven = [p for p in net if p == 0]
    gross_pos    = [p for p in gross if p > 0]
    gross_neg    = [p for p in gross if p <= 0]

    # WHY: Old code used 0.001 divisor fallback → produced fake PF=50,000 when
    #      there are no losing trades. Cap at 99.99 instead — clearly a sentinel.
    # CHANGED: April 2026 — proper PF cap + additional metrics
    def _safe_pf(wins_sum, losses_sum):
        if losses_sum < 1.0:
            return 99.99 if wins_sum > 0 else 0.0
        return round(wins_sum / losses_sum, 2)

    n_winners   = len(net_winners)
    n_losers    = len(net_losers)
    n_breakeven = len(net_breakeven)
    win_rate    = n_winners / len(trades) * 100
    avg_w       = float(np.mean(net_winners)) if net_winners else 0.0
    avg_l       = float(np.mean(net_losers))  if net_losers  else 0.0

    # Expectancy: direct mean — no decomposition needed, handles break-evens correctly
    expectancy = float(np.mean(net))

    # Risk:Reward ratio
    rr_ratio = abs(avg_w / avg_l) if avg_l != 0 else 0.0

    # Consistency (Sharpe-ish)
    std_pips   = float(np.std(net)) if len(net) > 1 else 0.0
    sharpe_ish = round(float(np.mean(net)) / std_pips, 2) if std_pips > 0 else 0.0

    # Streak analysis (break-even trades reset both streaks — they are neither wins nor losses)
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for p in net:
        if p > 0:
            cur_win += 1; cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif p < 0:
            cur_loss += 1; cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:  # break-even
            cur_win = 0; cur_loss = 0

    # Trade frequency
    trades_per_day = days_per_trade = 0.0
    try:
        first_t    = pd.to_datetime(trades[0].get('entry_time', ''))
        last_t     = pd.to_datetime(trades[-1].get('entry_time', ''))
        total_days = max(1, (last_t - first_t).days)
        trades_per_day = round(len(trades) / total_days, 2)
        days_per_trade = round(total_days / len(trades), 1)
    except Exception:
        pass

    # WHY (T2b): trades_per_day is an aggregate ratio and gives the same
    #      number for a rule that fires evenly vs a rule that fires
    #      300× in one month and 0× the rest of the year. For prop-firm
    #      EAs we need to REJECT the second kind because evaluation is
    #      calendar-time, not trade-count. These two metrics measure
    #      time-distribution directly from entry_time.
    # CHANGED: April 2026 — T2b — time-distribution metrics
    active_month_coverage = 0.0
    longest_dormant_days  = 0.0
    total_calendar_days   = 0
    try:
        _min_per_month = 2
        try:
            import config_loader as _cs_cl
            _min_per_month = int(_cs_cl.load().get('compute_stats_min_per_month', 2))
        except Exception:
            pass

        _entry_times_raw = [t.get('entry_time', '') for t in trades]
        _entry_times = pd.to_datetime(pd.Series(_entry_times_raw), errors='coerce')
        _entry_times = _entry_times.dropna().sort_values().reset_index(drop=True)

        if len(_entry_times) >= 2:
            _bt_start = _entry_times.iloc[0]
            _bt_end   = _entry_times.iloc[-1]
            total_calendar_days = max(1, (_bt_end - _bt_start).days)

            # Metric 1: fraction of calendar months with >= _min_per_month trades
            _months_series = _entry_times.dt.to_period('M')
            _counts_per_month = _months_series.value_counts()
            _active_months = int((_counts_per_month >= _min_per_month).sum())
            _first_period = _bt_start.to_period('M')
            _last_period  = _bt_end.to_period('M')
            _total_months = int((_last_period - _first_period).n) + 1
            if _total_months > 0:
                active_month_coverage = round(_active_months / _total_months, 3)

            # Metric 2: longest inter-trade gap in days
            _gaps = _entry_times.diff().dt.days.dropna()
            if len(_gaps) > 0:
                longest_dormant_days = round(float(_gaps.max()), 1)
    except Exception:
        pass

    cum  = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    dd   = peak - cum
    max_dd_pips = float(dd.max()) if len(dd) > 0 else 0.0

    # Recovery factor: net profit / max drawdown
    recovery_factor = round(float(sum(net)) / max_dd_pips, 2) if max_dd_pips > 0 else 0.0

    # END_OF_DATA bias detection: trades forced-closed at data boundary
    # WHY: Trades still open at the end of the dataset are closed at the
    #      last candle's close price, which biases results (losers get a
    #      better price than SL would give). If >15% of trades are END_OF_DATA,
    #      the stats are unreliable.
    # CHANGED: April 2026 — END_OF_DATA bias reporting
    _eod_trades = sum(1 for t in trades if t.get('exit_reason') == 'END_OF_DATA')
    _eod_pct = round(_eod_trades / len(trades) * 100, 1) if trades else 0.0
    if _eod_pct > 15:
        log.warning(f"  [STATS] {_eod_pct}% of trades ({_eod_trades}/{len(trades)}) "
                    f"exited at END_OF_DATA — results may be biased. "
                    f"Consider extending candle data or using shorter max_hold.")

    stats = {
        "total_trades":      len(trades),
        "win_rate":          round(win_rate, 1),
        "avg_pips":          round(float(np.mean(gross)), 1),
        "net_avg_pips":      round(float(np.mean(net)), 1),
        "total_pips":        round(float(sum(gross)), 0),
        "net_total_pips":    round(float(sum(net)), 0),
        "profit_factor":     _safe_pf(sum(gross_pos), abs(sum(gross_neg))),
        "net_profit_factor": _safe_pf(sum(net_winners), abs(sum(net_losers))),
        "max_dd_pips":       round(max_dd_pips, 0),
        "total_costs":       round(costs, 0),
        "avg_winner":        round(avg_w, 1),
        "avg_loser":         round(avg_l, 1),
        "best_trade":        round(max(net), 1),
        "worst_trade":       round(min(net), 1),
        # Extended metrics
        "expectancy":        round(expectancy, 2),
        "risk_reward_ratio": round(rr_ratio, 2),
        "std_pips":          round(std_pips, 1),
        "sharpe_ish":        sharpe_ish,
        "max_win_streak":    max_win_streak,
        "max_loss_streak":   max_loss_streak,
        "trades_per_day":    trades_per_day,
        "days_per_trade":    days_per_trade,
        "recovery_factor":   recovery_factor,
        "winners":           n_winners,
        "losers":            n_losers,
        "breakeven":         n_breakeven,
        "min_hold_violations": sum(1 for t in trades if t.get('candles_held', 999) <= 0),
        "end_of_data_trades": _eod_trades,
        "end_of_data_pct":   _eod_pct,
        # WHY (T2b): Time-distribution metrics. Consumers (ranking,
        #      filters, UI) can read these to reject regime-concentrated
        #      rules before they reach EA generation.
        # CHANGED: April 2026 — T2b
        "active_month_coverage": active_month_coverage,
        "longest_dormant_days":  longest_dormant_days,
        "total_calendar_days":   total_calendar_days,
    }

    # Dollar P&L equity tracking — only run_backtest sets dollar_pnl.
    # Vectorized + fast_backtest set 'net_profit' instead. Try both.
    # CHANGED: April 2026 — accept dollar_pnl OR net_profit
    dollar_pnls = []
    for t in trades:
        d = t.get("dollar_pnl")
        if d is None:
            d = t.get("net_profit")  # vectorized/fast use this name
        if d is not None:
            dollar_pnls.append(d)
    if dollar_pnls:
        cum_d  = np.cumsum(dollar_pnls)
        peak_d = np.maximum.accumulate(cum_d)
        dd_d   = peak_d - cum_d
        # Infer account_size from first trade's lot_size + dollar_pnl (approximate)
        stats["total_dollar_pnl"] = round(float(sum(dollar_pnls)), 2)
        stats["max_dd_dollars"]   = round(float(dd_d.max()), 2)

    return stats


def run_comparison_matrix(candles_path, timeframe="H1",
                          report_path=None, rule_indices=None,
                          exit_strategies=None, direction="BUY",
                          start_date=None, end_date=None,
                          spread_pips=2.5, commission_pips=0.0,
                          slippage_pips=0.0,
                          pip_size=0.01,
                          account_size=None, risk_per_trade_pct=1.0,
                          default_sl_pips=150.0, pip_value_per_lot=1.0,
                          progress_callback=None,
                          use_safety_stops=True,
                          # NEW: firm-specific breach thresholds (optional)
                          breach_account_size=100_000,
                          breach_daily_dd_limit_pct=5.0,
                          breach_total_dd_limit_pct=10.0,
                          breach_daily_safety_pct=4.0,
                          breach_total_safety_pct=8.0,
                          # WHY (Phase A.42): max_trades_per_day=0 means no limit
                          #      (default, preserves pre-A.42 behavior). Any positive
                          #      integer limits how many trades the backtester opens
                          #      per calendar day. Passed through to fast_backtest.
                          # CHANGED: April 2026 — Phase A.42
                          max_trades_per_day=0,
                          # WHY (Phase A.45): When True, generate every possible
                          #      OR-combination (pairs, triples, etc.) of the
                          #      selected rules instead of the legacy All+Top3+Top5
                          #      combos. Default False = pre-A.45 behavior.
                          # CHANGED: April 2026 — Phase A.45
                          combine_all_rules=False,
                          # WHY (leverage): Passed through to fast_backtest.
                          #      0 = no margin check (backward compat).
                          # CHANGED: April 2026 — margin-aware lot sizing
                          leverage=0, contract_size=100.0,
                          funded_protect=False,
                          compound_equity=False,
                          # WHY: hard_close_hour / cooldown_candles passed through
                          #      to fast_backtest for full parity with the live EA.
                          # CHANGED: April 2026 — hard close + cooldown (MT5 parity)
                          hard_close_hour=-1,
                          cooldown_candles=0,
                          # WHY: variable_spread / max_spread_pips passed through
                          #      to fast_backtest for session-based spread model.
                          # CHANGED: April 2026 — session-based variable spread model
                          variable_spread=False,
                          max_spread_pips=0,
                          # WHY: Per-firm asymmetric swap rates. Passed through to
                          #      fast_backtest. Default 0 = no swap modeled
                          #      (backward compat when no firm is selected).
                          # CHANGED: April 2026 — asymmetric swap
                          swap_long_pips_per_night=0.0,
                          swap_short_pips_per_night=0.0,
                          # WHY: Gate management exits (trail, BE, indicator) during
                          #      first N minutes after entry — matches EA MinHoldMinutes.
                          #      Patched onto each management-style exit before the run.
                          #      0 = disabled (backward compat).
                          # CHANGED: April 2026 — min hold parity with MT5 EA
                          min_hold_minutes=0):
    """
    Run the full comparison matrix: rule combos x exit strategies.

    progress_callback: optional callable(current, total, combo_name) for UI updates.
    Returns dict with "matrix", "rules_tested", "exits_tested", "elapsed".
    """
    _stop_requested.clear()  # Reset from any previous run
    log.info("=" * 70)
    log.info("STRATEGY BACKTESTER — Vectorized Comparison Matrix")
    log.info("=" * 70)
    start_time = time.time()

    # ── Load H1 candles (used for trade simulation) ──────────────────────────
    candles_path = os.path.abspath(candles_path)
    data_dir     = os.path.dirname(candles_path)

    log.info(f"\nLoading candle data: {candles_path}")
    candles_df = pd.read_csv(candles_path, encoding='utf-8-sig')

    # Auto-detect timestamp column
    if 'timestamp' not in candles_df.columns:
        ts_col = None
        for col in candles_df.columns:
            if col.lower().strip() in ('time', 'date', 'datetime', 'open_time', 'opentime'):
                ts_col = col
                break
        if ts_col is None:
            ts_col = candles_df.columns[0]
        candles_df = candles_df.rename(columns={ts_col: 'timestamp'})

    candles_df['timestamp'] = normalize_timestamp(candles_df['timestamp'])
    candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)
    log.info(f"  {len(candles_df)} candles "
             f"({candles_df['timestamp'].min()} to {candles_df['timestamp'].max()})")

    # Timezone validation: candle timestamps must be tz-naive (assumed UTC).
    # WHY: MT5 brokers often export data in server time (UTC+2/+3). If
    #      tz-aware timestamps slip through, session-based indicators
    #      (London open, NY close) compute on the wrong hours.
    # CHANGED: April 2026 — timezone validation
    _ts = candles_df['timestamp']
    if hasattr(_ts.dt, 'tz') and _ts.dt.tz is not None:
        log.warning(f"  [TZ] Candle timestamps have timezone {_ts.dt.tz} — "
                    f"stripping to tz-naive (assumed UTC). If your data is NOT "
                    f"in UTC, session indicators will be misaligned.")
        candles_df['timestamp'] = _ts.dt.tz_localize(None)
    else:
        log.info(f"  [TZ] Timestamps are tz-naive (assumed UTC)")

    from shared.data_validator import check_backtest_data_quality
    dq_warnings = check_backtest_data_quality(candles_df, timeframe=timeframe)
    if dq_warnings:
        log.warning("\nDATA QUALITY WARNINGS:")
        for w in dq_warnings:
            log.info(f"  [{w['severity'].upper()}] {w['message']}")
        # WHY (Phase A.4 hotfix): old code was `log.info()` with no args —
        #      a leftover from the print()→log.info() conversion pass
        #      documented in shared/logging_setup.py. logger.info()
        #      requires msg as a positional argument and raised
        #      TypeError: info() missing 1 required positional argument: 'msg'
        #      the moment dq_warnings was non-empty. Log an empty string
        #      as a blank-line separator to preserve the original intent.
        # CHANGED: April 2026 — Phase A.4
        log.info("")

    # ── Load rules first — needed to extract required indicators ────────────
    all_rules = load_rules_from_report(report_path)
    rules = ([all_rules[i] for i in rule_indices if i < len(all_rules)]
             if rule_indices is not None else all_rules)

    # WHY (Bug fix): exit_strategies default was populated ~200 lines below,
    #      so _extract_required_indicators always received None and missed
    #      ATR/RSI columns needed by exit strategies (e.g. ATRBased).
    #      Moved here so the extraction sees the real exit strategy list.
    # WHY (T1c): Pass entry TF so ATR exits size SL/TP from matching-TF ATR.
    # CHANGED: April 2026 — T1c
    if exit_strategies is None:
        exit_strategies = get_default_exit_strategies(pip_size=pip_size, entry_tf=timeframe)

    # WHY: Apply min hold from firm to every management-style exit.
    #      Fast-path exits (FixedSLTP/ATRFixedSLTP) use broker-level SL/TP
    #      and aren't gated. Management exits gate ratchet/BE/indicator
    #      logic during the first min_hold_minutes after entry.
    # CHANGED: April 2026 — auto-apply min hold to management exits
    if min_hold_minutes and int(min_hold_minutes) > 0:
        _mh_seconds = int(min_hold_minutes) * 60
        from project2_backtesting.exit_strategies import (
            TrailingStop, ATRBreakevenTrail, PSARExit,
            ATRTrailing, IndicatorExit, HybridExit,
        )
        _mgmt_classes = (TrailingStop, ATRBreakevenTrail, PSARExit,
                         ATRTrailing, IndicatorExit, HybridExit)
        _patched = 0
        for _exit in exit_strategies:
            if isinstance(_exit, _mgmt_classes):
                try:
                    _exit.min_hold_seconds = _mh_seconds
                    _patched += 1
                except Exception:
                    pass
        log.info(f"[MIN HOLD] Applied {min_hold_minutes}min ({_mh_seconds}s) "
                 f"to {_patched} management-style exits")

    # WHY (T1c): Make ATR column resolution visible. A run on M5 entries
    #      should see atr_column='M5_mt5_atr_14' flow into every ATRBased and
    #      ATRTrailing instance. This one log line makes misconfiguration
    #      obvious when user runs multi-TF backtests.
    # CHANGED: April 2026 — T1c diagnostic
    log.info(f"[T1c] entry_tf={timeframe} → ATR exits will use "
             f"{timeframe}_mt5_atr_14 for SL/TP sizing")

    # Extract which indicators each TF actually needs — skips the other ~575
    # WHY (Phase A.42.1): Pass exit strategies so their indicator
    #      requirements (ATR, RSI, etc.) are included in the load set.
    # CHANGED: April 2026 — Phase A.42.1
    required_indicators = _extract_required_indicators(all_rules, exit_strategies)
    total_needed = sum(len(v) for v in required_indicators.values())
    log.info(f"\n[BACKTESTER] Required indicators per TF ({total_needed} total vs 595 full):")
    for tf, inds in required_indicators.items():
        preview = ', '.join(inds[:5]) + ('...' if len(inds) > 5 else '')
        log.info(f"  {tf}: {len(inds)} indicators — {preview}")

    # ── Build multi-timeframe indicator DataFrame ────────────────────────────
    # Each TF CSV is loaded, only the needed indicators are computed (prefixed
    # e.g. H4_adx_14), then merged onto the H1 spine via merge_asof.
    # Results are cached as parquet; separate cache files for partial vs full builds.
    log.info(f"\nBuilding multi-timeframe indicators (M5 / M15 / H1 / H4 / D1)...")
    indicators_df = build_multi_tf_indicators(
        data_dir, candles_df['timestamp'], required_indicators=required_indicators,
        entry_tf=timeframe)
    log.info(f"  Total indicator columns: {len(indicators_df.columns)}")

    # ── Compute SMART & REGIME features if any rules reference them ───────────────
    smart_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                    if c['feature'].startswith('SMART_')}
    regime_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                     if c['feature'].startswith('REGIME_')}

    if smart_needed:
        log.info(f"\n[BACKTESTER] Rules use {len(smart_needed)} SMART features — computing...")
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            # SMART features need hour_of_day and open_time columns
            if 'hour_of_day' not in indicators_df.columns:
                indicators_df['hour_of_day'] = candles_df['timestamp'].dt.hour
            if 'open_time' not in indicators_df.columns:
                indicators_df['open_time'] = candles_df['timestamp'].astype(str)

            indicators_df = _add_tf_divergences(indicators_df)
            indicators_df = _add_indicator_dynamics(indicators_df)
            indicators_df = _add_alignment_scores(indicators_df)
            indicators_df = _add_session_intelligence(indicators_df)
            indicators_df = _add_volatility_regimes(indicators_df)
            indicators_df = _add_price_action(indicators_df)
            indicators_df = _add_momentum_quality(indicators_df)

            smart_cols = [c for c in indicators_df.columns if c.startswith('SMART_')]
            log.info(f"  Added {len(smart_cols)} SMART features")
        except ImportError:
            log.warning("smart_features module not found — SMART conditions will not match")
        except Exception as e:
            log.warning(f"Failed to compute SMART features: {e}")

    if regime_needed:
        log.info(f"\n[BACKTESTER] Rules use {len(regime_needed)} REGIME features — computing...")
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            indicators_df = _add_regime_features(indicators_df)
            regime_cols = [c for c in indicators_df.columns if c.startswith('REGIME_')]
            log.info(f"  Added {len(regime_cols)} REGIME features")
        except ImportError:
            log.warning("smart_features module not found — REGIME conditions will not match")
        except Exception as e:
            log.warning(f"Failed to compute REGIME features: {e}")

    # ── Verify all rule features are available ──────────────────────────────
    needed    = {c["feature"] for r in rules for c in r.get("conditions", [])}
    available = set(indicators_df.columns)
    found     = needed & available
    missing   = needed - available

    # Separate SMART & REGIME features from regular indicators for clearer reporting
    smart_features = {f for f in needed if f.startswith('SMART_')}
    regime_features = {f for f in needed if f.startswith('REGIME_')}
    regular_features = needed - smart_features - regime_features
    smart_found = smart_features & available
    smart_missing = smart_features - available
    regime_found = regime_features & available
    regime_missing = regime_features - available
    regular_found = regular_features & available
    regular_missing = regular_features - available

    log.info(f"\n[BACKTESTER] Feature availability check:")
    log.info(f"  Regular indicators: {len(regular_found)}/{len(regular_features)} found"
             + (f" — MISSING: {sorted(regular_missing)[:5]}" + ("..." if len(regular_missing) > 5 else "")
                if regular_missing else " ✓"))
    if smart_features:
        log.info(f"  SMART features:     {len(smart_found)}/{len(smart_features)} found"
                 + (f" — MISSING: {sorted(smart_missing)[:5]}" + ("..." if len(smart_missing) > 5 else "")
                    if smart_missing else " ✓"))
    if regime_features:
        log.info(f"  REGIME features:    {len(regime_found)}/{len(regime_features)} found"
                 + (f" — MISSING: {sorted(regime_missing)[:5]}" + ("..." if len(regime_missing) > 5 else "")
                    if regime_missing else " ✓"))

    if missing:
        log.warning(f"{len(missing)} features missing — rules using them will match 0 trades")
        if regular_missing and not smart_missing:
            log.info(f"  → Regular indicators missing — check that CSV files contain OHLCV data")
        elif smart_missing and not regular_missing:
            log.info(f"  → SMART features missing — ensure smart_features module is available")

    # ── Build rule combos ────────────────────────────────────────────────────
    # WHY (Phase A.30): Old code built one combo per rule and passed
    #      the matrix-level `direction` (which defaulted to "BUY")
    #      into every fast_backtest call. For a bidirectional bot
    #      whose rules carry action="BOTH", every signal was forced
    #      into a BUY trade — so roughly half the signals traded the
    #      wrong direction by definition and win rates collapsed to
    #      ~15%.
    #
    #      Fix: read each rule's `action` field and expand the combo
    #      list per direction. A BUY-only rule becomes one combo. A
    #      SELL-only rule becomes one combo. A BOTH rule becomes TWO
    #      combos — one tested as BUY and one tested as SELL, with
    #      direction-tagged names so the matrix display makes the
    #      split obvious. Each combo carries its own `direction`
    #      field which the matrix loop passes to fast_backtest
    #      below, instead of relying on the function default.
    #
    #      This roughly doubles the matrix for bidirectional bots
    #      (10 rules × 12 exits = 120 → ~240) but the runtime cost
    #      is linear and the user gets honest per-direction win
    #      rates instead of a meaningless 50/50 mush.
    # CHANGED: April 2026 — Phase A.30
    def _a30_rule_directions(rule_obj):
        """Return list of directions to test for one rule.

        Reads the rule's `action` field (the key step6 and the
        Phase A.27 analyze.py both write). Defaults to ['BUY']
        for legacy rules that have neither — preserves old
        behavior on rule sets predating A.27.
        """
        a = str(rule_obj.get('action', 'BUY')).upper().strip()
        if a in ('BUY', 'LONG'):
            return ['BUY']
        if a in ('SELL', 'SHORT'):
            return ['SELL']
        if a in ('BOTH', 'BIDIRECTIONAL', 'EITHER'):
            return ['BUY', 'SELL']
        # Unknown / missing → default to BUY only (matches old behavior)
        return ['BUY']

    rule_combos = []

    # ── Individual rules (always present) ──
    for i, r in enumerate(rules):
        for _dir in _a30_rule_directions(r):
            # WHY: Rules from analysis_report.json don't have _saved_rule_id.
            #      Generate a descriptive label from conditions if missing.
            # CHANGED: April 2026 — descriptive labels for all rules
            _rule_label = r.get('_saved_rule_id', '')
            if not _rule_label:
                _rule_label = r.get('rule_id', '')
            if not _rule_label:
                # Build from conditions like BUY_H1_5c
                _rl_dir = r.get('direction', r.get('action', 'BUY'))
                _rl_tf = r.get('entry_timeframe', r.get('entry_tf', 'XX'))
                _rl_nc = len(r.get('conditions', []))
                import hashlib as _rl_hl
                _rl_conds = str(sorted(str(c) for c in r.get('conditions', [])))
                _rl_exit = r.get('exit_name', r.get('exit_class', ''))
                _rl_hash = _rl_hl.md5((_rl_conds + _rl_exit).encode()).hexdigest()[:4]
                _rule_label = f"{_rl_dir}_{_rl_tf}_{_rl_nc}c_{_rl_hash}"
            rule_combos.append({
                "name":      f"{_rule_label} ({_dir})",
                "rules":     [r],
                "indices":   [i],
                "direction": _dir,
            })

    def _a30_rules_for_dir(rule_list, dir_name):
        picked     = []
        picked_idx = []
        for j, rr in enumerate(rule_list):
            allowed = _a30_rule_directions(rr)
            if dir_name in allowed:
                picked.append(rr)
                picked_idx.append(j)
        return picked, picked_idx

    if len(rules) > 1 and combine_all_rules:
        # ═══════════════════════════════════════════════════════════════
        # Phase A.45: Generate ALL possible OR-combinations of selected
        #      rules (pairs, triples, quads, etc.). Each combo means:
        #      if ANY rule in the combo fires, a trade opens.
        #
        #      Produces 2^N - 1 - N additional combos (excluding the
        #      empty set and individuals already added above).
        #      Per-direction: only rules compatible with BUY/SELL are
        #      included in each directional combo.
        # CHANGED: April 2026 — Phase A.45
        # ═══════════════════════════════════════════════════════════════
        import itertools
        for combo_size in range(2, len(rules) + 1):
            for idx_tuple in itertools.combinations(range(len(rules)), combo_size):
                combo_label = "+".join(str(j + 1) for j in idx_tuple)
                for _dir in ('BUY', 'SELL'):
                    _dir_rules = []
                    _dir_indices = []
                    for j in idx_tuple:
                        if _dir in _a30_rule_directions(rules[j]):
                            _dir_rules.append(rules[j])
                            _dir_indices.append(j)
                    if _dir_rules:
                        rule_combos.append({
                            "name":      f"Rules {combo_label} ({_dir})",
                            "rules":     _dir_rules,
                            "indices":   _dir_indices,
                            "direction": _dir,
                        })

    elif len(rules) > 1:
        # ── Legacy combo mode (A.30): All combined, Top 3, Top 5 ──
        # WHY (Phase A.30): For multi-rule combos, build BUY and SELL
        #      versions separately.
        # CHANGED: April 2026 — Phase A.30
        for _dir in ('BUY', 'SELL'):
            _all_rules, _all_idx = _a30_rules_for_dir(rules, _dir)
            if _all_rules:
                rule_combos.append({
                    "name":      f"All rules combined ({_dir})",
                    "rules":     _all_rules,
                    "indices":   _all_idx,
                    "direction": _dir,
                })

        if len(rules) >= 3:
            for _dir in ('BUY', 'SELL'):
                _top, _top_idx = _a30_rules_for_dir(rules[:3], _dir)
                if _top:
                    rule_combos.append({
                        "name":      f"Top 3 rules ({_dir})",
                        "rules":     _top,
                        "indices":   _top_idx,
                        "direction": _dir,
                    })

        if len(rules) >= 5:
            for _dir in ('BUY', 'SELL'):
                _top, _top_idx = _a30_rules_for_dir(rules[:5], _dir)
                if _top:
                    rule_combos.append({
                        "name":      f"Top 5 rules ({_dir})",
                        "rules":     _top,
                        "indices":   _top_idx,
                        "direction": _dir,
                    })

    # WHY (Phase A.30): Diagnostic log so the user can see the
    #      per-direction expansion in the console and confirm it
    #      matches their expectations. Counts of BUY-only vs
    #      SELL-only vs BOTH rules at the top of the run.
    # CHANGED: April 2026 — Phase A.30
    _a30_buy_count  = sum(1 for r in rules if 'BUY'  in _a30_rule_directions(r))
    _a30_sell_count = sum(1 for r in rules if 'SELL' in _a30_rule_directions(r))
    log.info(
        f"  [A.30] Per-rule direction: "
        f"{_a30_buy_count} rules trade BUY, "
        f"{_a30_sell_count} rules trade SELL, "
        f"{len(rule_combos)} total combos after expansion"
    )

    total = len(rule_combos) * len(exit_strategies)
    log.info(f"\nTesting {len(rule_combos)} rule combos x {len(exit_strategies)} exit strategies "
             f"= {total} combinations  |  spread={spread_pips} pips  commission={commission_pips} pips")

    # ── Pre-trim once: apply date filter + skip warmup rows ──────────────────
    # WHY: run_backtest copies DataFrames on every call and re-applies date filters.
    #      Pre-trimming once saves len(rule_combos)*len(exit_strategies) copies.
    _c = candles_df.iloc[200:].reset_index(drop=True)
    _i = indicators_df.iloc[200:].reset_index(drop=True)
    if start_date:
        _sd = pd.Timestamp(start_date)
        mask = _c['timestamp'] >= _sd
        _c = _c[mask].reset_index(drop=True)
        _i = _i[mask].reset_index(drop=True)
    if end_date:
        _ed = pd.Timestamp(end_date)
        mask = _c['timestamp'] <= _ed
        _c = _c[mask].reset_index(drop=True)
        _i = _i[mask].reset_index(drop=True)
    log.info(f"  Pre-trimmed to {len(_c)} candles for matrix loop")

    matrix = []
    count  = 0

    # ── Phase A.38a: Reset regime filter log cache for this run ───────
    # WHY (Phase A.38a): log_filter_summary_once dedupes by (subset, action)
    #      key for the process lifetime. Clearing at the start of each
    #      comparison matrix run means the user sees one fresh summary
    #      per Run Backtest click — even if they switched strictness
    #      presets between clicks.
    # CHANGED: April 2026 — Phase A.38a
    try:
        from project2_backtesting.regime_filter_runtime import reset_logging_cache
        reset_logging_cache()
    except Exception:
        pass

    _was_stopped = False
    for combo_idx, combo in enumerate(rule_combos, start=1):
        if _stop_requested.is_set():
            log.info(f"[BACKTESTER] Stop requested — saving {len(matrix)} results computed so far")
            _was_stopped = True
            break
        for exit_strat in exit_strategies:
            if _stop_requested.is_set():
                _was_stopped = True
                break
            count += 1

            # WHY (Phase A.30): Use the combo's per-direction value
            #      instead of the matrix-level `direction` default.
            #      Old code passed `direction=direction` for every
            #      combo, which forced every rule to BUY because the
            #      matrix-level default is "BUY" and the panel never
            #      overrides it. Each combo now carries its own
            #      direction set by the per-direction expansion
            #      above, so a "Rule 3 (SELL)" combo actually opens
            #      SELL trades and "Rule 3 (BUY)" actually opens BUY
            #      trades.
            # CHANGED: April 2026 — Phase A.30
            _a30_combo_direction = combo.get("direction", direction)

            trades = fast_backtest(
                df=_c, ind=_i,
                rules=combo["rules"], exit_strategy=exit_strat,
                direction=_a30_combo_direction,
                pip_size=pip_size,
                spread_pips=spread_pips, commission_pips=commission_pips,
                slippage_pips=slippage_pips,
                account_size=account_size,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
                # WHY (Phase A.42): Enforce daily trade limit per user setting.
                # CHANGED: April 2026 — Phase A.42
                max_trades_per_day=max_trades_per_day,
                # WHY (leverage): Pass margin constraints through.
                # CHANGED: April 2026 — margin-aware lot sizing
                leverage=leverage, contract_size=contract_size,
                compound_equity=compound_equity,
                hard_close_hour=hard_close_hour,
                cooldown_candles=cooldown_candles,
                # WHY: Pass variable spread through from matrix config.
                # CHANGED: April 2026 — session-based variable spread model
                variable_spread=variable_spread,
                max_spread_pips=max_spread_pips,
                # WHY: data_dir enables tick-aware exit ambiguity resolution.
                #      Already derived from candles_path above.
                # CHANGED: April 2026 — tick data for exit ambiguity resolution
                data_dir=data_dir,
                # WHY: Per-firm asymmetric swap passed from matrix config.
                # CHANGED: April 2026 — asymmetric swap
                swap_long_pips_per_night=swap_long_pips_per_night,
                swap_short_pips_per_night=swap_short_pips_per_night,
            )
            stats = compute_stats(trades)

            # WHY: combo["name"] is "BUY_D1_4c_a670 (BUY)" — same prefix repeats
            #      for every exit strategy on the same rule, which looks like
            #      non-unique IDs. Extract just the short rule hash (a670) and
            #      combine with the exit name so each combo reads "a670_Fixed_SLTP_c296".
            # CHANGED: April 2026 — short unique ID per rule×exit combo
            _exit_tag = (exit_strat.name
                .replace(' ', '_').replace('/', '').replace('-', '_')
                .replace('+', '_').replace('(', '').replace(')', ''))[:12]
            _exit_hash = hashlib.md5(
                (exit_strat.name + str(exit_strat.params)).encode()
            ).hexdigest()[:4]
            _rule_combo_id = f'#{combo_idx}_' + combo["name"].split(' ')[0] + '_' + _exit_tag + '_' + _exit_hash

            result = {
                "rules":        combo["rules"],        # actual rule conditions for validator
                "rule_combo":   _rule_combo_id,
                "rule_indices": combo["indices"],
                # WHY: Direction was only embedded in rule_combo name string
                #      like "(BUY)". Downstream tools parsed the name to guess
                #      direction — fragile. Now saved explicitly.
                # CHANGED: April 2026 — explicit direction in result
                "direction":    _a30_combo_direction,
                "exit_strategy": exit_strat.describe(),
                "exit_name":    exit_strat.name,
                "exit_class":   type(exit_strat).__name__,
                "exit_params":  exit_strat.params,
                "stats":        stats,
                "trades":       trades,
                "signals_before_regime_filter": getattr(fast_backtest, '_last_sig_before', 0),
                "signals_after_regime_filter":  getattr(fast_backtest, '_last_sig_after', 0),
            }
            matrix.append(result)

            # Call progress callback with result dict (backward compatible)
            if progress_callback:
                # WHY (Phase A.5 hotfix): old code passed bare `stats` as the
                #      4th arg. stats contains the performance metrics the
                #      panel reads for per-combo lines (total_trades,
                #      win_rate, net_total_pips, net_profit_factor) but it
                #      does NOT contain rule_combo, exit_name, exit_class —
                #      those live on the outer `result` dict. The panel's
                #      _update_best() reads b['rule_combo'] and b['exit_name']
                #      to render the "🏆 best so far" label, and crashed with
                #      KeyError: 'rule_combo' on every tick that produced
                #      trades. Pass a merged dict: flatten stats at top level
                #      (so the panel's existing reads still work) and add the
                #      three identity fields needed by _update_best().
                # CHANGED: April 2026 — Phase A.5 — merge identity + stats
                # WHY (Phase A.38b): Carry regime filter signal counts into
                #      the progress payload so the Run Backtest panel can
                #      show "N trades (M before filter)". Read from the
                #      function-attribute stash fast_backtest wrote above.
                # CHANGED: April 2026 — Phase A.38b
                _a38b_sig_before = getattr(fast_backtest, '_last_sig_before', 0)
                _a38b_sig_after  = getattr(fast_backtest, '_last_sig_after',  0)
                _progress_payload = {
                    **stats,
                    'rule_combo': _rule_combo_id,
                    'exit_name':  exit_strat.name,
                    'exit_class': type(exit_strat).__name__,
                    'signals_before_regime_filter': _a38b_sig_before,
                    'signals_after_regime_filter':  _a38b_sig_after,
                }
                _unique_name = _progress_payload['rule_combo']
                try:
                    progress_callback(count, total, _unique_name, _progress_payload)
                except TypeError:
                    progress_callback(count, total, _unique_name)
            elif count % 10 == 0 or count == total:
                log.info(f"  [{count}/{total}] {_progress_payload['rule_combo']}")

    # WHY: Old sort by net_total_pips alone ranks a 10k-trade marginal-edge
    #      strategy above a 100-trade high-expectancy one. Users aiming at
    #      prop-firm DD limits need an ordering that punishes strategies
    #      whose max_dd_pips is large relative to their typical losers.
    # CHANGED: April 2026 — risk-adjusted matrix ranking
    # WHY (T2b): Same checkbox-gated logic as the multi-TF path. Uses the
    #      run_settings dict plumbed through by the panel. When keys are
    #      missing (CLI callers that don't build UI), both flags default
    #      OFF → exact pre-T2b / T1a behavior.
    # CHANGED: April 2026 — T2b v3 — checkbox-gated filter + weight
    try:
        from shared.ranking import (
            risk_adjusted_score_weighted,
            passes_time_distribution_filter,
        )
        # run_settings is injected into rows by the panel after this
        # function returns. Inside run_comparison_matrix, rows may not
        # carry run_settings yet — default both flags OFF (T1a behavior).
        _td_filter_on = False
        _td_weight_on = False
        for _r in matrix:
            _rs = _r.get('run_settings', {})
            if _rs:
                _td_filter_on = bool(_rs.get('td_filter_enabled', False))
                _td_weight_on = bool(_rs.get('td_weight_enabled', False))
                break

        _key_fn = lambda x: risk_adjusted_score_weighted(x, use_td_weight=_td_weight_on)

        if _td_filter_on:
            _passing = [r for r in matrix if passes_time_distribution_filter(r)]
            _failing = [r for r in matrix if r not in _passing]
            _passing.sort(key=_key_fn, reverse=True)
            _failing.sort(key=_key_fn, reverse=True)
            matrix[:] = _passing + _failing
        else:
            matrix.sort(key=_key_fn, reverse=True)
    except Exception:
        matrix.sort(key=lambda x: x["stats"]["net_total_pips"], reverse=True)

    elapsed = time.time() - start_time

    log.info(f"\n{'=' * 70}")
    log.info(f"BACKTEST COMPLETE in {elapsed:.1f}s — {total} combinations")
    log.info(f"\nTop 5 by net pips (after {spread_pips} pip spread):")
    for m in matrix[:5]:
        s = m["stats"]
        # WHY: compute_stats always stores win_rate as percent (0-100). The old
        #      `wr > 1` band-aid was dead — kept here as a comment so no one
        #      reintroduces the inconsistent format expectation.
        # CHANGED: April 2026 — remove dead band-aid
        wr = s['win_rate']
        wr_str = f"{wr:.1f}%"
        log.info(f"  {m['rule_combo']:20s} x {m['exit_name']:15s}: "
                 f"{s['total_trades']:>4d} trades, WR {wr_str:>6s}, "
                 f"Net PF {s['net_profit_factor']:>5.2f}, "
                 f"Net {s['net_total_pips']:>+8.0f} pips  (gross {s['total_pips']:>+8.0f})")
    log.info("=" * 70)

    # WHY: Shows where P/L is going — user can see what swap is actually
    #      costing on the best strategy without inspecting individual trades.
    # CHANGED: April 2026 — diagnostic cost breakdown
    try:
        log.info("")
        log.info("Cost breakdown — best strategy in this run:")
        if matrix and matrix[0].get('trades'):
            _best   = matrix[0]
            _btrds  = _best['trades']
            _n      = len(_btrds)
            if _n > 0:
                _spread_total  = sum(t.get('cost_spread_pips', 0) for t in _btrds)
                _comm_total    = sum(t.get('cost_commission_pips', 0) for t in _btrds)
                _swap_total    = sum(t.get('cost_swap_pips', 0) for t in _btrds)
                _swap_nights_t = sum(t.get('swap_nights', 0) for t in _btrds)
                _net           = sum(t.get('net_pips', 0) for t in _btrds)
                # WHY: Derive gross from net + costs so the breakdown
                #      reconciles by construction. The 'pips' field on
                #      fast and slow paths has subtly different definitions
                #      (commission included on fast, not slow), so deriving
                #      _gross from sum(pips) was unreliable. Net + signed
                #      costs is path-agnostic and always balances exactly.
                # CHANGED: April 2026 — derive gross from net for honest reconcile
                _gross         = _net - _spread_total - _comm_total - _swap_total
                log.info(f"  Strategy: {_best.get('rule_combo','?')} x "
                         f"{_best.get('exit_name','?')}")
                log.info(f"  Trades:               {_n:>8d}")
                log.info(f"  Gross pips:           {_gross:>+10.0f}")
                log.info(f"  - Spread:             {_spread_total:>+10.0f}  "
                         f"(avg {_spread_total/_n:.1f}/trade)")
                log.info(f"  - Commission:         {_comm_total:>+10.0f}  "
                         f"(avg {_comm_total/_n:.1f}/trade)")
                log.info(f"  - Swap:               {_swap_total:>+10.0f}  "
                         f"({_swap_nights_t} nights, avg {_swap_total/_n:.1f}/trade)")
                log.info(f"  Net pips:             {_net:>+10.0f}")
                log.info(f"  Swap rates used:  long={swap_long_pips_per_night:+.2f}  "
                         f"short={swap_short_pips_per_night:+.2f} pips/night")
        else:
            log.info("  (no trades to summarise)")
    except Exception as _cbd_e:
        log.warning(f"  [cost breakdown] {_cbd_e}")
    log.info("=" * 70)

    # ── Save outputs ─────────────────────────────────────────────────────────
    output_dir = os.path.join(_here, 'outputs')
    os.makedirs(output_dir, exist_ok=True)

    summary = []
    for m in matrix:
        # Compute breach stats for this strategy
        # WHY: safety_pct=None disables safety stops (passes None through to simulator).
        #      Old code hardcoded firm parameters; now they're parameters with
        #      firm-default values, so callers can pass actual firm config.
        # CHANGED: April 2026 — parameterized breach thresholds
        _safety_daily = breach_daily_safety_pct if use_safety_stops else None
        _safety_total = breach_total_safety_pct if use_safety_stops else None
        breaches = count_dd_breaches(
            m["trades"],
            account_size=breach_account_size,
            daily_dd_limit_pct=breach_daily_dd_limit_pct,
            total_dd_limit_pct=breach_total_dd_limit_pct,
            daily_dd_safety_pct=_safety_daily,
            total_dd_safety_pct=_safety_total,
            funded_protect=funded_protect,
        )

        result = {
            "rule_combo":      m["rule_combo"],
            "rule_indices":    m.get("rule_indices", []),
            "rules":           m.get("rules", []),
            "exit_strategy":   m["exit_strategy"],
            "exit_name":       m["exit_name"],
            "exit_class":      m.get("exit_class", ""),
            "exit_params":     m.get("exit_params", {}),
            "spread_pips":     spread_pips,
            "commission_pips": commission_pips,
            **m["stats"],
            "trades": m["trades"],
            "breaches": breaches,
            "signals_before_regime_filter": m.get("signals_before_regime_filter", 0),
            "signals_after_regime_filter":  m.get("signals_after_regime_filter", 0),
        }
        summary.append(result)

    # FIX 2: ensure every result row carries its entry_tf (multi-TF run tags each row)
    # WHY: downstream tools (Refiner, Validator, EA Generator) read entry_tf per-row
    #      to load the correct candle file. Without this, rows from multi-TF runs lose
    #      their TF tag when saved to JSON.
    # CHANGED: April 2026 — multi-TF support
    for row in summary:
        if 'entry_tf' not in row:
            row['entry_tf'] = timeframe
        if isinstance(row.get('stats'), dict) and 'entry_tf' not in row['stats']:
            row['stats']['entry_tf'] = row['entry_tf']

    unique_tfs = sorted(set(r.get('entry_tf', timeframe) for r in summary))
    top_level_tf = 'multi' if len(unique_tfs) > 1 else (unique_tfs[0] if unique_tfs else timeframe)

    # ── Phase A.48: Save trades to separate file, strip from main JSON ──
    # WHY (Phase A.48): Storing full trade lists inside backtest_matrix.json
    #      caused 3-4 GB JSON files and out-of-memory crashes on multi-TF
    #      runs. Fix: save trades to a compact separate file keyed by
    #      combo index. The main JSON carries stats only (trade_count
    #      field replaces trades array). The A.47 export button reads
    #      from the separate trades file.
    # CHANGED: April 2026 — Phase A.48

    # Save trades to separate per-TF file
    trades_path = os.path.join(output_dir, f'backtest_trades_{timeframe}.json')
    try:
        trades_data = {}
        for idx, m in enumerate(summary):
            # WHY: Persist the original enumerate index so the refiner can
            #      find the right trades-file key even after the panel
            #      re-sorts backtest_matrix.json by score.
            # CHANGED: April 2026 — _trades_key fix for sorted-matrix mismatch
            m['_trades_key'] = idx
            t_list = m.get('trades', [])
            if t_list:
                trades_data[str(idx)] = t_list
        with open(trades_path, 'w', encoding='utf-8') as tf_file:
            json.dump(trades_data, tf_file, default=str)
        log.info(f"Saved: {trades_path} ({len(trades_data)} combos with trades)")
    except Exception as _te:
        log.warning(f"Could not save trades file: {_te}")

    # Strip trades from summary for the main JSON (keeps it small)
    for m in summary:
        m['trade_count'] = len(m.get('trades', []))
        m.pop('trades', None)

    summary_path = os.path.join(output_dir, 'backtest_matrix.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump({
            "generated_at":      time.strftime("%Y-%m-%d %H:%M"),
            "entry_timeframe":   top_level_tf,
            "max_trades_per_day": max_trades_per_day,
            "tested_timeframes": unique_tfs,
            "combinations":      total,
            "stopped_early":     _was_stopped,
            "completed_combos":  count,
            "total_combos":      total,
            "elapsed_seconds":   round(elapsed, 1),
            "spread_pips":       spread_pips,
            "commission_pips":   commission_pips,
            "slippage_pips":     slippage_pips,
            "results":           summary,
        }, f, indent=2, default=str)
    log.info(f"Saved: {summary_path}")

    csv_path = os.path.join(output_dir, 'backtest_matrix.csv')
    try:
        pd.DataFrame(summary).to_csv(csv_path, index=False)
        log.info(f"Saved: {csv_path}")
    except Exception:
        pass

    # WHY (Hotfix): Old code returned `matrix` which has stats nested
    #      under 'stats' key and NO 'breaches'. The panel's combined
    #      multi-TF save wrote this to backtest_matrix.json, causing
    #      View Results to show no breach/DD/survival data.
    #      Return `summary` instead — it has stats flattened at top
    #      level, breaches computed, and trade_count set. Trades are
    #      already stripped (line 2517-2519).
    # CHANGED: April 2026 — return summary instead of matrix
    return {
        "matrix":       summary,
        "rules_tested": [c["name"] for c in rule_combos],
        "exits_tested": [e.describe() for e in exit_strategies],
        "elapsed":      elapsed,
    }


if __name__ == "__main__":
    # WHY: Read entry TF from config instead of hardcoding H1
    try:
        from project2_backtesting.panels.configuration import load_config
        cfg = load_config()
        entry_tf = cfg.get('winning_scenario', 'H1')
    except Exception:
        entry_tf = 'H1'

    try:
        from shared.instrument_config import get_candle_path, get_active_symbol
        candles_path = get_candle_path(get_active_symbol(), entry_tf)
    except Exception:
        # WHY: Use selected data source, not hardcoded path.
        # CHANGED: April 2026 — data source from config
        try:
            from shared.data_sources import resolve_data_dir
            _sb_dir = resolve_data_dir()
        except Exception:
            _sb_dir = os.path.join(_here, '..', 'data')
        candles_path = os.path.join(_sb_dir, f'xauusd_{entry_tf}.csv')
        if not os.path.exists(candles_path):
            candles_path = os.path.join(_sb_dir, f'XAUUSD_{entry_tf}.csv')

    if not os.path.exists(candles_path):
        log.error(f"Candle data not found: {candles_path}")
        sys.exit(1)

    run_comparison_matrix(candles_path, timeframe=entry_tf)

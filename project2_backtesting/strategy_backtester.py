"""
STRATEGY BACKTESTER — Tests entry rules x exit strategies on historical candle data.

Vectorized entry detection: builds boolean masks over all 128K candles at once,
then only loops through the handful of signal candles to simulate exits.
This is ~100x faster than the naive candle-by-candle loop.

Multi-timeframe indicators: loads M5/M15/H1/H4/D1 CSVs, computes the full
indicator set for each timeframe (prefixed e.g. H1_rsi_14), then aligns
everything to the H1 timestamp spine using merge_asof (no look-ahead bias).
Indicator DataFrames are cached as parquet so the first run is slow (~5 min)
but subsequent runs load in seconds.
"""
import sys
import os
import time
import json

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

# Timeframes to load, in order: smallest first so merge_asof steps up cleanly
_TIMEFRAMES = ["M5", "M15", "H1", "H4", "D1"]


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
    entry_rules = [r for r in rules if r.get('prediction') == 'WIN']
    print(f"Loaded {len(entry_rules)} entry rules (WIN prediction) from {len(rules)} total rules")
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


def _extract_required_indicators(rules):
    """
    Get the set of indicator names needed by the rules, grouped by timeframe.
    When rules use SMART or REGIME features, also includes all base indicators
    that those features depend on.
    """
    required = {}
    has_smart = False
    has_regime = False

    for rule in rules:
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

    # Separate cache file for partial vs full builds — they must not conflict
    if needed_indicators:
        cache_suffix = "_" + "_".join(sorted(needed_indicators))[:50]
        cache_path = os.path.join(data_dir, f".cache_{tf}_partial{cache_suffix}.parquet")
    else:
        cache_path = os.path.join(data_dir, f".cache_{tf}_indicators.parquet")

    if not os.path.exists(csv_path):
        print(f"  WARNING: {csv_path} not found — skipping {tf}")
        return None

    csv_mtime   = os.path.getmtime(csv_path)
    cache_valid = (
        os.path.exists(cache_path)
        and os.path.getmtime(cache_path) > csv_mtime
    )

    if cache_valid:
        print(f"  {tf}: loading from cache ({cache_path})")
        df = pd.read_parquet(cache_path)
        # Handle old caches that may have 'index' instead of 'timestamp'
        if 'timestamp' not in df.columns:
            if 'index' in df.columns:
                df = df.rename(columns={'index': 'timestamp'})
            else:
                # Cache is corrupt — delete and recompute
                print(f"  {tf}: cache missing timestamp column — deleting and recomputing")
                os.remove(cache_path)
                cache_valid = False
        if cache_valid:
            df['timestamp'] = normalize_timestamp(df['timestamp'])
            df = df.dropna(subset=['timestamp']).reset_index(drop=True)
            return df

    if needed_indicators:
        compute_groups = indicator_utils.map_rule_indicators_to_compute_groups(needed_indicators)
        print(f"  {tf}: computing {len(needed_indicators)} indicators "
              f"(groups: {', '.join(compute_groups)}) from {csv_path} ...")
    else:
        compute_groups = None
        print(f"  {tf}: computing all indicators from {csv_path} ...")

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
        # compute_indicators sets timestamp as the DataFrame index
        ind = indicator_utils.compute_indicators(candles, only=compute_groups, prefix=f"{tf}_")
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

    ind.to_parquet(cache_path, index=False)
    print(f"  {tf}: {len(ind.columns) - 1} indicators cached -> {cache_path}")
    return ind


def build_multi_tf_indicators(data_dir, entry_timestamps, required_indicators=None):
    """
    Load and align all timeframe indicators onto the entry timeframe's timestamp spine.

    For each TF, uses merge_asof with direction='backward' so each entry candle
    receives the most recent indicator values from that TF without look-ahead.

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

        merged = pd.merge_asof(
            combined[['timestamp']],
            tf_ind,
            on='timestamp',
            direction='backward',
        )
        ind_cols = [c for c in merged.columns if c != 'timestamp']
        combined = pd.concat([combined, merged[ind_cols]], axis=1)

    combined = combined.drop(columns=['timestamp']).reset_index(drop=True)
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


def _vectorized_fixed_sltp_exits(df, signal_indices, signal_rule_ids, rules,
                                  exit_strategy, direction, pip_size,
                                  spread_pips, commission_pips, slippage_pips,
                                  account_size, risk_per_trade_pct,
                                  default_sl_pips, pip_value_per_lot,
                                  swap_cost_per_lot_per_night=0):
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
    """
    trades = []

    sl_pips = exit_strategy.sl_pips
    tp_pips = exit_strategy.tp_pips

    # Pre-extract numpy arrays (read-only, no copy)
    all_opens  = df['open'].values.astype(float)
    all_highs  = df['high'].values.astype(float)
    all_lows   = df['low'].values.astype(float)
    all_closes = df['close'].values.astype(float)
    all_times  = df['timestamp'].values

    index_positions = {idx: pos for pos, idx in enumerate(df.index)}
    occupied_until_idx = -1

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue

        rule_id   = int(signal_rule_ids.loc[sig_idx])
        entry_pos = index_positions.get(sig_idx, 0)

        if entry_pos + 1 >= len(df):
            continue

        entry_price = all_opens[entry_pos + 1]

        if direction == "BUY":
            entry_price += (spread_pips + slippage_pips) * pip_size
        else:
            entry_price -= slippage_pips * pip_size

        entry_time = all_times[entry_pos + 1]

        # Compute SL/TP levels
        if direction == "BUY":
            sl_price = entry_price - sl_pips * pip_size
            tp_price = entry_price + tp_pips * pip_size
        else:
            sl_price = entry_price + sl_pips * pip_size
            tp_price = entry_price - tp_pips * pip_size

        # Get future candle arrays from entry+2 onward
        start = entry_pos + 2
        if start >= len(df):
            continue

        future_highs = all_highs[start:]
        future_lows  = all_lows[start:]
        future_opens = all_opens[start:]

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
            # No SL/TP hit — close at end of data
            exit_pos     = len(df) - 1
            exit_price   = all_closes[-1]
            exit_time    = all_times[-1]
            exit_reason  = "END_OF_DATA"
            candles_held = len(future_highs)

        # P&L
        if direction == "BUY":
            pnl_pips = (exit_price - entry_price) / pip_size
        else:
            pnl_pips = (entry_price - exit_price) / pip_size

        net_pips = pnl_pips - commission_pips

        # Swap costs (Wednesday triple-roll aware)
        swap_cost_pips = 0.0
        if swap_cost_per_lot_per_night > 0:
            entry_dt    = pd.Timestamp(entry_time)
            exit_dt     = pd.Timestamp(exit_time)
            swap_nights = _count_swap_nights(entry_dt, exit_dt)
            if swap_nights > 0:
                swap_cost_pips = (swap_nights * swap_cost_per_lot_per_night) / pip_value_per_lot
                net_pips -= swap_cost_pips

        # Lot sizing
        lot_size = 0.01
        if account_size and risk_per_trade_pct > 0 and default_sl_pips > 0:
            risk_dollars = account_size * (risk_per_trade_pct / 100)
            lot_size = max(0.01, round(risk_dollars / (default_sl_pips * pip_value_per_lot), 2))

        net_profit = net_pips * pip_value_per_lot * lot_size

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
        })

        occupied_until_idx = df.index[exit_pos]

    return trades


def run_backtest(candles_df, indicators_df, rules, exit_strategy,
                 direction="BUY", start_date=None, end_date=None,
                 pip_size=0.01, max_open_trades=1,
                 spread_pips=2.5, commission_pips=0.0,
                 slippage_pips=0.0,
                 account_size=None, risk_per_trade_pct=1.0,
                 default_sl_pips=150.0, pip_value_per_lot=10.0,
                 swap_cost_per_lot_per_night=0.0,
                 news_blackout_minutes=0):
    """
    Run a single backtest using vectorized entry detection.

    1. Build a boolean mask over the full indicator DataFrame to find all signal candles.
    2. Loop only over signal candles (~50-500) to simulate individual trade exits.

    Returns list of trade dicts.
    """
    trades = []

    # ── Date filter ──────────────────────────────────────────────────────────
    df  = candles_df.copy().reset_index(drop=True)
    ind = indicators_df.copy().reset_index(drop=True)

    # Ensure same length before filtering
    min_len = min(len(df), len(ind))
    if len(df) != len(ind):
        print(f"  [run_backtest] WARNING: candles ({len(df)}) and indicators ({len(ind)}) length mismatch — trimming to {min_len}")
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
            print(f"  [run_backtest] Computed {len(smart_cols)} SMART features")
        except ImportError:
            print("  WARNING: smart_features module not found — SMART conditions will not match")
        except Exception as e:
            print(f"  WARNING: Error computing SMART features: {e}")

    # Compute REGIME features if needed
    if regime_needed and not any(c.startswith('REGIME_') for c in ind.columns):
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            ind = _add_regime_features(ind)
            regime_cols = [c for c in ind.columns if c.startswith('REGIME_')]
            print(f"  [run_backtest] Computed {len(regime_cols)} REGIME features")
        except ImportError:
            print("  WARNING: smart_features module not found — REGIME conditions will not match")
        except Exception as e:
            print(f"  WARNING: Failed to compute SMART features: {e}")

    # ── VECTORIZED: build entry signal mask ──────────────────────────────────
    signal_mask     = pd.Series(False, index=ind.index)
    signal_rule_ids = pd.Series(-1,    index=ind.index, dtype=int)

    for rule_idx, rule in enumerate(rules):
        rule_mask  = pd.Series(True, index=ind.index)
        valid_rule = True

        for cond in rule.get("conditions", []):
            col = cond["feature"]
            if col not in ind.columns:
                valid_rule = False
                break
            col_data = ind[col]
            op       = cond["operator"]
            val      = cond["value"]
            if op == "<=":
                rule_mask &= (col_data <= val)
            elif op == ">":
                rule_mask &= (col_data > val)
            elif op == "<":
                rule_mask &= (col_data < val)
            elif op == ">=":
                rule_mask &= (col_data >= val)

        if not valid_rule:
            continue

        rule_mask = rule_mask.fillna(False)

        # First rule wins per candle
        new_signals = rule_mask & ~signal_mask
        signal_mask |= rule_mask
        signal_rule_ids[new_signals] = rule_idx

    signal_indices = df.index[signal_mask].tolist()

    # ── Use vectorized exit for FixedSLTP (10-50x faster) ────────────────────
    # WHY: FixedSLTP has constant SL/TP levels — numpy finds the exit candle
    #      in microseconds per trade vs milliseconds for the iterrows loop.
    # CHANGED: April 2026 — vectorized FixedSLTP path
    from project2_backtesting.exit_strategies import FixedSLTP
    if isinstance(exit_strategy, FixedSLTP) and signal_indices:
        return _vectorized_fixed_sltp_exits(
            df, signal_indices, signal_rule_ids, rules,
            exit_strategy, direction, pip_size,
            spread_pips, commission_pips, slippage_pips,
            account_size, risk_per_trade_pct,
            default_sl_pips, pip_value_per_lot,
            swap_cost_per_lot_per_night,
        )

    # ── Simulate trades from signal candles ──────────────────────────────────
    occupied_until_idx = -1   # index of last candle in current open trade

    # Build positional lookup once (integer positions for slicing forward)
    index_positions = {idx: pos for pos, idx in enumerate(df.index)}

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue

        rule_id       = int(signal_rule_ids.loc[sig_idx])
        entry_pos_int = index_positions.get(sig_idx, 0)

        # Enter at the NEXT candle's open to avoid look-ahead bias
        if entry_pos_int + 1 >= len(df):
            continue
        next_candle = df.iloc[entry_pos_int + 1]

        # News blackout filter
        if news_blackout_minutes > 0:
            from project2_backtesting.news_calendar import is_news_blackout
            entry_time = next_candle['timestamp']
            if is_news_blackout(entry_time, news_blackout_minutes):
                continue  # skip this entry

        # Determine direction first (needed for slippage sign)
        if direction == "BOTH":
            rule_obj  = rules[rule_id] if rule_id < len(rules) else {}
            trade_dir = rule_obj.get("direction", "BUY")
        else:
            trade_dir = direction

        entry_price = float(next_candle["open"])
        # Apply random slippage against the trader (always a worse fill)
        if slippage_pips > 0:
            import random
            slip = random.uniform(0, slippage_pips) * pip_size
            if trade_dir == "BUY":
                entry_price += slip   # buy fills higher
            else:
                entry_price -= slip   # sell fills lower
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
            next_idx    = next_candle.name
            candle_dict = next_candle.to_dict()
            if next_idx in ind.index:
                candle_dict.update(ind.loc[next_idx].to_dict())
            exit_strategy.on_entry(candle_dict)

        # Scan forward from the candle after the entry candle
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
            occupied_until_idx = df.index[-1]

        pnl_pips = (exit_price - entry_price) / pip_size
        if trade_dir == "SELL":
            pnl_pips = -pnl_pips

        cost     = spread_pips + commission_pips
        net_pips = pnl_pips - cost

        # Swap costs for overnight holds (Wednesday triple-roll aware)
        swap_nights = 0
        swap_cost_pips = 0.0
        if swap_cost_per_lot_per_night > 0:
            entry_dt    = pd.to_datetime(entry_time)
            exit_dt     = pd.to_datetime(exit_time)
            swap_nights = _count_swap_nights(entry_dt, exit_dt)
            if swap_nights > 0:
                # Convert swap cost to pips (swap is $/lot/night, pip_value is $/pip/lot)
                swap_total_per_lot = swap_nights * swap_cost_per_lot_per_night
                swap_cost_pips = swap_total_per_lot / pip_value_per_lot
                net_pips -= swap_cost_pips

        # Position sizing and dollar P&L (optional, when account_size is provided)
        if account_size is not None:
            risk_dollars = account_size * (risk_per_trade_pct / 100.0)
            lot_size = risk_dollars / (default_sl_pips * pip_value_per_lot)
            # WHY: Silent min(lot_size, 100.0) hid absurdly large positions
            #      (e.g. 500-lot size on a $10M virtual account) and made stats
            #      look better than they would be on a real broker.
            # CHANGED: April 2026 — warn instead of silently capping
            if lot_size > 100.0:
                print(f"  [WARN] Computed lot size {lot_size:.1f} exceeds 100 — "
                      f"check account_size / risk_pct / sl_pips settings")
            lot_size   = max(0.01, lot_size)
            dollar_pnl = round(net_pips * pip_value_per_lot * lot_size, 2)
        else:
            lot_size   = None
            dollar_pnl = None

        trades.append({
            "entry_time":  entry_time,
            "exit_time":   exit_time,
            "direction":   trade_dir,
            # WHY: round(,2) truncates forex prices (5 decimal places).
            # CHANGED: April 2026 — use 5 decimal places like vectorized path
            "entry_price": round(entry_price, 5),
            "exit_price":  round(exit_price, 5),
            "pnl_pips":    round(pnl_pips, 1),
            "cost_pips":   round(cost, 1),
            "net_pips":    round(net_pips, 1),
            "exit_reason":  exit_reason,
            "candles_held": candles_held,
            "rule_id":      rule_id,
            "lot_size":     lot_size,
            "dollar_pnl":   dollar_pnl,
            "swap_nights":  swap_nights,
            "swap_cost_pips": round(swap_cost_pips, 1),
        })

    return trades


def fast_backtest(df, ind, rules, exit_strategy,
                  direction="BUY", pip_size=0.01,
                  spread_pips=2.5, commission_pips=0.0,
                  slippage_pips=0.0,
                  account_size=None, risk_per_trade_pct=1.0,
                  default_sl_pips=150.0, pip_value_per_lot=10.0):
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
    _skipped_count = 0   # FIX 12E: track SANE_PIP_LIMIT skips

    if len(df) == 0:
        return trades

    # ── VECTORIZED: build entry signal mask ──────────────────────────────
    # WHY: This is the only part that changes between iterations —
    #      different threshold values produce different masks.
    #      Everything else (indicator values, candle data) is identical.
    signal_mask     = pd.Series(False, index=ind.index)
    signal_rule_ids = pd.Series(-1,    index=ind.index, dtype=int)

    for rule_idx, rule in enumerate(rules):
        if rule.get('prediction') != 'WIN':
            continue
        rule_mask  = pd.Series(True, index=ind.index)
        valid_rule = True

        for cond in rule.get("conditions", []):
            col = cond.get("feature", "")
            if col not in ind.columns:
                valid_rule = False
                break
            col_data = ind[col]
            op       = cond.get("operator", ">")
            val      = cond.get("value", 0)
            if op == "<=":
                rule_mask &= (col_data <= val)
            elif op == ">":
                rule_mask &= (col_data > val)
            elif op == "<":
                rule_mask &= (col_data < val)
            elif op == ">=":
                rule_mask &= (col_data >= val)

        if not valid_rule:
            continue

        rule_mask = rule_mask.fillna(False)
        new_signals = rule_mask & ~signal_mask
        signal_mask |= rule_mask
        signal_rule_ids[new_signals] = rule_idx

    signal_indices = df.index[signal_mask].tolist()

    if not signal_indices:
        return trades

    # ── Use vectorized exit for FixedSLTP ────────────────────────────────
    # WHY: Same optimization as run_backtest — vectorized exit detection.
    # CHANGED: April 2026 — vectorized FixedSLTP in fast_backtest
    from project2_backtesting.exit_strategies import FixedSLTP
    if isinstance(exit_strategy, FixedSLTP):
        return _vectorized_fixed_sltp_exits(
            df, signal_indices, signal_rule_ids, rules,
            exit_strategy, direction, pip_size,
            spread_pips, commission_pips, slippage_pips,
            account_size, risk_per_trade_pct,
            default_sl_pips, pip_value_per_lot,
        )

    # ── Simulate trades from signal candles ──────────────────────────────
    occupied_until_idx = -1
    index_positions = {idx: pos for pos, idx in enumerate(df.index)}

    for sig_idx in signal_indices:
        if sig_idx <= occupied_until_idx:
            continue

        entry_pos_int = index_positions.get(sig_idx, 0)
        if entry_pos_int + 1 >= len(df):
            continue
        next_candle = df.iloc[entry_pos_int + 1]

        entry_time  = next_candle['timestamp']
        entry_price = float(next_candle['open'])

        if direction == "BUY":
            entry_price += (spread_pips + slippage_pips) * pip_size
        else:
            entry_price -= slippage_pips * pip_size

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
        pos_info = {
            'entry_price':      entry_price,
            'direction':        direction,
            'entry_time':       entry_time,
            'entry_candle':     df.iloc[entry_pos_int],
            'candles_held':     0,    # incremented per candle below
            'minutes_held':     0,    # incremented per candle below
            'current_pnl_pips': 0,    # updated per candle below
            'highest_since_entry': float(df.iloc[entry_pos_int]['high']),
            'lowest_since_entry':  float(df.iloc[entry_pos_int]['low']),
        }

        # Some exits (ATRBased) need on_entry hook for setup
        if hasattr(exit_strategy, 'on_entry'):
            try:
                exit_strategy.on_entry(df.iloc[entry_pos_int])
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

        result = None
        exit_idx = -1
        for ci in range(len(future_candles)):
            candle = future_candles.iloc[ci]

            # Update position state before calling exit strategy
            pos_info['candles_held'] = ci
            pos_info['minutes_held'] = ci * candle_minutes
            close = float(candle['close'])
            pos_info['current_pnl_pips'] = (
                (close - entry_price) / pip_size if direction == "BUY"
                else (entry_price - close) / pip_size
            )
            pos_info['highest_since_entry'] = max(pos_info['highest_since_entry'], float(candle['high']))
            pos_info['lowest_since_entry']  = min(pos_info['lowest_since_entry'],  float(candle['low']))

            try:
                step_result = exit_strategy.on_new_candle(candle, pos_info)
            except Exception as e:
                if ci == 0:  # log once per trade, not per candle
                    print(f"  [fast_backtest exit error] {type(exit_strategy).__name__}: {e}")
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
        SANE_PIP_LIMIT = 50_000  # 50K pips = $5000 on XAUUSD per 0.01 lot
        if abs(pips) > SANE_PIP_LIMIT:
            _skipped_count += 1
            if _skipped_count <= 5:   # log first few occurrences
                print(f"  [SKIP] Absurd pips: {pips:.0f} "
                      f"(entry={entry_price:.2f}, exit={exit_price:.2f}, "
                      f"reason={exit_reason}) — likely silent exit failure")
            continue

        net_pips = pips - commission_pips

        lot_size = 0.01
        if account_size and risk_per_trade_pct > 0:
            risk_dollars = account_size * (risk_per_trade_pct / 100)
            lot_size = risk_dollars / (default_sl_pips * pip_value_per_lot) if default_sl_pips > 0 else 0.01
            lot_size = max(0.01, round(lot_size, 2))

        net_profit = net_pips * pip_value_per_lot * lot_size

        trade = {
            'entry_time':   str(entry_time),
            'exit_time':    str(exit_time),
            'entry_price':  round(entry_price, 5),
            'exit_price':   round(exit_price, 5),
            'direction':    direction,
            'pips':         round(pips, 1),
            'net_pips':     round(net_pips, 1),
            'net_profit':   round(net_profit, 2),
            'lot_size':     lot_size,
            'exit_reason':  exit_reason,
            'rule_id':      int(signal_rule_ids.loc[sig_idx]),
        }
        trades.append(trade)

        # Mark occupied candles
        occupied_until_idx = df.index[min(entry_pos_int + 1 + exit_idx, len(df) - 1)]

    if _skipped_count > 0:
        print(f"  [fast_backtest] Skipped {_skipped_count} trade(s) with absurd pips "
              f"(SANE_PIP_LIMIT={50_000}). Check exit strategy for silent failures.")

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

    cum  = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    dd   = peak - cum
    max_dd_pips = float(dd.max()) if len(dd) > 0 else 0.0

    # Recovery factor: net profit / max drawdown
    recovery_factor = round(float(sum(net)) / max_dd_pips, 2) if max_dd_pips > 0 else 0.0

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
                          default_sl_pips=150.0, pip_value_per_lot=10.0,
                          progress_callback=None,
                          use_safety_stops=True,
                          # NEW: firm-specific breach thresholds (optional)
                          breach_account_size=100_000,
                          breach_daily_dd_limit_pct=5.0,
                          breach_total_dd_limit_pct=10.0,
                          breach_daily_safety_pct=4.0,
                          breach_total_safety_pct=8.0):
    """
    Run the full comparison matrix: rule combos x exit strategies.

    progress_callback: optional callable(current, total, combo_name) for UI updates.
    Returns dict with "matrix", "rules_tested", "exits_tested", "elapsed".
    """
    print("=" * 70)
    print("STRATEGY BACKTESTER — Vectorized Comparison Matrix")
    print("=" * 70)
    start_time = time.time()

    # ── Load H1 candles (used for trade simulation) ──────────────────────────
    candles_path = os.path.abspath(candles_path)
    data_dir     = os.path.dirname(candles_path)

    print(f"\nLoading candle data: {candles_path}")
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
    print(f"  {len(candles_df)} candles "
          f"({candles_df['timestamp'].min()} to {candles_df['timestamp'].max()})")

    from shared.data_validator import check_backtest_data_quality
    dq_warnings = check_backtest_data_quality(candles_df, timeframe=timeframe)
    if dq_warnings:
        print("\nDATA QUALITY WARNINGS:")
        for w in dq_warnings:
            print(f"  [{w['severity'].upper()}] {w['message']}")
        print()

    # ── Load rules first — needed to extract required indicators ────────────
    all_rules = load_rules_from_report(report_path)
    rules = ([all_rules[i] for i in rule_indices if i < len(all_rules)]
             if rule_indices is not None else all_rules)

    # Extract which indicators each TF actually needs — skips the other ~575
    required_indicators = _extract_required_indicators(all_rules)
    total_needed = sum(len(v) for v in required_indicators.values())
    print(f"\n[BACKTESTER] Required indicators per TF ({total_needed} total vs 595 full):")
    for tf, inds in required_indicators.items():
        preview = ', '.join(inds[:5]) + ('...' if len(inds) > 5 else '')
        print(f"  {tf}: {len(inds)} indicators — {preview}")

    # ── Build multi-timeframe indicator DataFrame ────────────────────────────
    # Each TF CSV is loaded, only the needed indicators are computed (prefixed
    # e.g. H4_adx_14), then merged onto the H1 spine via merge_asof.
    # Results are cached as parquet; separate cache files for partial vs full builds.
    print(f"\nBuilding multi-timeframe indicators (M5 / M15 / H1 / H4 / D1)...")
    indicators_df = build_multi_tf_indicators(
        data_dir, candles_df['timestamp'], required_indicators=required_indicators)
    print(f"  Total indicator columns: {len(indicators_df.columns)}")

    # ── Compute SMART & REGIME features if any rules reference them ───────────────
    smart_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                    if c['feature'].startswith('SMART_')}
    regime_needed = {c['feature'] for r in rules for c in r.get('conditions', [])
                     if c['feature'].startswith('REGIME_')}

    if smart_needed:
        print(f"\n[BACKTESTER] Rules use {len(smart_needed)} SMART features — computing...")
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
            print(f"  Added {len(smart_cols)} SMART features")
        except ImportError:
            print("  WARNING: smart_features module not found — SMART conditions will not match")
        except Exception as e:
            print(f"  WARNING: Failed to compute SMART features: {e}")

    if regime_needed:
        print(f"\n[BACKTESTER] Rules use {len(regime_needed)} REGIME features — computing...")
        try:
            from project1_reverse_engineering.smart_features import _add_regime_features
            indicators_df = _add_regime_features(indicators_df)
            regime_cols = [c for c in indicators_df.columns if c.startswith('REGIME_')]
            print(f"  Added {len(regime_cols)} REGIME features")
        except ImportError:
            print("  WARNING: smart_features module not found — REGIME conditions will not match")
        except Exception as e:
            print(f"  WARNING: Failed to compute REGIME features: {e}")

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

    print(f"\n[BACKTESTER] Feature availability check:")
    print(f"  Regular indicators: {len(regular_found)}/{len(regular_features)} found"
          + (f" — MISSING: {sorted(regular_missing)[:5]}" + ("..." if len(regular_missing) > 5 else "")
             if regular_missing else " ✓"))
    if smart_features:
        print(f"  SMART features:     {len(smart_found)}/{len(smart_features)} found"
              + (f" — MISSING: {sorted(smart_missing)[:5]}" + ("..." if len(smart_missing) > 5 else "")
                 if smart_missing else " ✓"))
    if regime_features:
        print(f"  REGIME features:    {len(regime_found)}/{len(regime_features)} found"
              + (f" — MISSING: {sorted(regime_missing)[:5]}" + ("..." if len(regime_missing) > 5 else "")
                 if regime_missing else " ✓"))

    if missing:
        print(f"  WARNING: {len(missing)} features missing — rules using them will match 0 trades")
        if regular_missing and not smart_missing:
            print(f"  → Regular indicators missing — check that CSV files contain OHLCV data")
        elif smart_missing and not regular_missing:
            print(f"  → SMART features missing — ensure smart_features module is available")

    # ── Build rule combos ────────────────────────────────────────────────────
    rule_combos = [{"name": f"Rule {i+1}", "rules": [r], "indices": [i]}
                   for i, r in enumerate(rules)]
    if len(rules) > 1:
        rule_combos.append({"name": "All rules combined", "rules": rules,
                             "indices": list(range(len(rules)))})
        if len(rules) >= 3:
            rule_combos.append({"name": "Top 3 rules", "rules": rules[:3],
                                 "indices": [0, 1, 2]})
        if len(rules) >= 5:
            rule_combos.append({"name": "Top 5 rules", "rules": rules[:5],
                                 "indices": [0, 1, 2, 3, 4]})

    if exit_strategies is None:
        exit_strategies = get_default_exit_strategies(pip_size=pip_size)

    total = len(rule_combos) * len(exit_strategies)
    print(f"\nTesting {len(rule_combos)} rule combos x {len(exit_strategies)} exit strategies "
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
    print(f"  Pre-trimmed to {len(_c)} candles for matrix loop")

    matrix = []
    count  = 0

    for combo in rule_combos:
        for exit_strat in exit_strategies:
            count += 1

            trades = fast_backtest(
                df=_c, ind=_i,
                rules=combo["rules"], exit_strategy=exit_strat,
                direction=direction,
                pip_size=pip_size,
                spread_pips=spread_pips, commission_pips=commission_pips,
                slippage_pips=slippage_pips,
                account_size=account_size,
                risk_per_trade_pct=risk_per_trade_pct,
                default_sl_pips=default_sl_pips,
                pip_value_per_lot=pip_value_per_lot,
            )
            stats = compute_stats(trades)

            result = {
                "rules":        combo["rules"],        # actual rule conditions for validator
                "rule_combo":   combo["name"],
                "rule_indices": combo["indices"],
                "exit_strategy": exit_strat.describe(),
                "exit_name":    exit_strat.name,
                "exit_class":   type(exit_strat).__name__,
                "exit_params":  exit_strat.params,
                "stats":        stats,
                "trades":       trades,
            }
            matrix.append(result)

            # Call progress callback with result dict (backward compatible)
            if progress_callback:
                try:
                    # Try new signature with result_dict parameter
                    progress_callback(count, total, f"{combo['name']} x {exit_strat.name}", stats)
                except TypeError:
                    # Fall back to old 3-parameter signature
                    progress_callback(count, total, f"{combo['name']} x {exit_strat.name}")
            elif count % 10 == 0 or count == total:
                print(f"  [{count}/{total}] {combo['name']} x {exit_strat.describe()}")

    # Sort by net total pips descending (real profitability after costs)
    matrix.sort(key=lambda x: x["stats"]["net_total_pips"], reverse=True)

    elapsed = time.time() - start_time

    print(f"\n{'=' * 70}")
    print(f"BACKTEST COMPLETE in {elapsed:.1f}s — {total} combinations")
    print(f"\nTop 5 by net pips (after {spread_pips} pip spread):")
    for m in matrix[:5]:
        s = m["stats"]
        # WHY: compute_stats always stores win_rate as percent (0-100). The old
        #      `wr > 1` band-aid was dead — kept here as a comment so no one
        #      reintroduces the inconsistent format expectation.
        # CHANGED: April 2026 — remove dead band-aid
        wr = s['win_rate']
        wr_str = f"{wr:.1f}%"
        print(f"  {m['rule_combo']:20s} x {m['exit_name']:15s}: "
              f"{s['total_trades']:>4d} trades, WR {wr_str:>6s}, "
              f"Net PF {s['net_profit_factor']:>5.2f}, "
              f"Net {s['net_total_pips']:>+8.0f} pips  (gross {s['total_pips']:>+8.0f})")
    print("=" * 70)

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

    summary_path = os.path.join(output_dir, 'backtest_matrix.json')
    # WHY: Downstream panels (validator, refiner, EA generator) need to know which
    #      entry TF was used so they load the correct candle file and calculate
    #      correct hold times.
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump({
            "generated_at":      time.strftime("%Y-%m-%d %H:%M"),
            "entry_timeframe":   top_level_tf,
            "tested_timeframes": unique_tfs,
            "combinations":      total,
            "elapsed_seconds":   round(elapsed, 1),
            "spread_pips":       spread_pips,
            "commission_pips":   commission_pips,
            "slippage_pips":     slippage_pips,
            "results":           summary,
        }, f, indent=2, default=str)
    print(f"Saved: {summary_path}")

    csv_path = os.path.join(output_dir, 'backtest_matrix.csv')
    pd.DataFrame(summary).to_csv(csv_path, index=False)
    print(f"Saved: {csv_path}")

    return {
        "matrix":       matrix,
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
        candles_path = os.path.join(_here, '..', 'data', f'xauusd_{entry_tf}.csv')

    if not os.path.exists(candles_path):
        print(f"ERROR: Candle data not found: {candles_path}")
        sys.exit(1)

    run_comparison_matrix(candles_path, timeframe=entry_tf)

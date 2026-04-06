"""
Scratch Discovery — build strategy from raw candle data using XGBoost.

1. Label every candle WIN/LOSS (candle_labeler.py)
2. Load indicators for every candle (from backtester cache)
3. Compute smart features for every candle
4. Train XGBoost on 130K+ labeled candles
5. Extract rules in analysis_report.json format
6. Output plugs directly into backtester

No robot trade history needed. Pure price-data-driven discovery.
"""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(_HERE, 'outputs')
RESULT_PATH = os.path.join(OUTPUT_DIR, 'discovery_scratch.json')

sys.path.insert(0, os.path.abspath(os.path.join(_HERE, '..')))


def run_scratch_discovery(
    candles_path=None,
    entry_timeframe=None,
    sl_pips=150,
    tp_pips=300,
    direction="BUY",
    max_hold_candles=50,
    pip_size=0.01,
    spread_pips=2.5,
    use_smart_features=True,
    max_rules=25,
    max_depth=4,
    n_estimators=300,
    min_coverage_pct=1.0,
    min_win_rate=0.55,
    train_test_split=0.7,
    prop_firm_name=None,
    prop_firm_data=None,
    compare_all_tfs=False,
    discovery_mode='quick',             # 'quick', 'deep', or 'exhaustive'
    enhance_grid_threshold=False,       # Level 1: test precise thresholds
    enhance_multi_exit=False,           # Level 2: test multiple SL/TP combos
    enhance_walkforward_score=False,    # Level 3: score rules with walk-forward
    enhance_feature_interactions=False, # Level 4: generate cross-indicator features
    progress_callback=None,
):
    """
    Full scratch discovery pipeline.
    Returns result dict and saves to discovery_scratch.json.

    New Parameters:
    - prop_firm_name: Optional prop firm name for Monte Carlo pass probability estimation
    - prop_firm_data: Optional prop firm config dict (DD limits, account size, etc.)
    - compare_all_tfs: If True, runs discovery on M5/M15/H1/H4 and returns comparison
    """
    start       = time.time()
    total_steps = 6

    # ── MULTI-TIMEFRAME COMPARISON MODE ───────────────────────────────────────
    if compare_all_tfs:
        timeframes = ["M5", "M15", "H1", "H4"]
        comparison_results = []

        for idx, tf in enumerate(timeframes):
            if progress_callback:
                progress_callback(idx + 1, len(timeframes),
                                 f"Running discovery for {tf}... ({idx+1}/{len(timeframes)})")

            # Run discovery for this timeframe
            try:
                tf_result = run_scratch_discovery(
                    candles_path=candles_path,
                    entry_timeframe=tf,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                    direction=direction,
                    max_hold_candles=max_hold_candles,
                    pip_size=pip_size,
                    spread_pips=spread_pips,
                    use_smart_features=use_smart_features,
                    max_rules=max_rules,
                    max_depth=max_depth,
                    n_estimators=n_estimators,
                    min_coverage_pct=min_coverage_pct,
                    min_win_rate=min_win_rate,
                    train_test_split=train_test_split,
                    prop_firm_name=prop_firm_name,
                    prop_firm_data=prop_firm_data,
                    compare_all_tfs=False,  # Don't recurse infinitely
                    progress_callback=None,  # Suppress nested progress
                )

                # Extract key metrics
                rules = tf_result.get('rules', [])
                best_wr = max([r.get('win_rate', 0) for r in rules]) if rules else 0
                best_pips = max([r.get('avg_pips', 0) for r in rules]) if rules else 0

                comparison_results.append({
                    'timeframe': tf,
                    'rule_count': len(rules),
                    'best_win_rate': round(best_wr, 3),
                    'best_avg_pips': round(best_pips, 1),
                    'base_win_rate': tf_result.get('base_win_rate', 0),
                    'candles_analyzed': tf_result.get('profile', {}).get('candles_analyzed', 0),
                })

            except Exception as e:
                print(f"[WARNING] Discovery failed for {tf}: {e}")
                comparison_results.append({
                    'timeframe': tf,
                    'error': str(e),
                })

        # Build comparison result
        comparison_result = {
            'comparison_mode': True,
            'timeframes': comparison_results,
            'settings': {
                'sl_pips': sl_pips,
                'tp_pips': tp_pips,
                'direction': direction,
                'max_hold_candles': max_hold_candles,
            },
            'timestamp': datetime.now().isoformat(),
        }

        # Save comparison result
        comparison_path = os.path.join(OUTPUT_DIR, 'discovery_tf_comparison.json')
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(comparison_path, 'w', encoding='utf-8') as f:
            json.dump(comparison_result, f, indent=2)

        if progress_callback:
            progress_callback(len(timeframes), len(timeframes),
                             f"Comparison complete! Results saved to discovery_tf_comparison.json")

        return comparison_result

    # ── NORMAL SINGLE-TF DISCOVERY ────────────────────────────────────────────

    def _cb(*args):
        if progress_callback:
            if len(args) == 2:
                progress_callback(args[0], total_steps, args[1])
            elif len(args) == 3:
                progress_callback(args[0], args[1], args[2])

    # Auto-detect candle data path if not provided
    if candles_path is None:
        project_root = os.path.abspath(os.path.join(_HERE, '..'))
        # Use entry_timeframe parameter or default to H1
        symbol = 'xauusd'
        tf = entry_timeframe if entry_timeframe else 'H1'
        candles_path = os.path.join(project_root, 'data', f'{symbol}_{tf}.csv')
        if not os.path.exists(candles_path):
            raise FileNotFoundError(
                f"{tf} candle data not found at {candles_path}\n"
                "Run the Data Pipeline first to load your candle history."
            )

    # ── Step 1: Label candles ─────────────────────────────────────────────────
    _cb(1, "Step 1/6: Labeling candles (WIN/LOSS)...")

    from project4_strategy_creation.candle_labeler import label_candles

    try:
        labels_df = label_candles(
            candles_path=candles_path,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            pip_size=pip_size,
            direction=direction,
            max_hold_candles=max_hold_candles,
            spread_pips=spread_pips,
            progress_callback=lambda cur, tot, msg: _cb(1, f"Labeling: {msg}"),
        )

        n_candles     = len(labels_df)
        win_rate_base = labels_df['label'].mean()
        print(f"[DEBUG] Labeling done: {n_candles} rows, base WR: {win_rate_base:.1%}")
    except Exception as e:
        print(f"[DEBUG] FAILED at labeling: {e}")
        import traceback
        traceback.print_exc()
        raise

    # ── Step 2: Load indicators for ALL candles ───────────────────────────────
    _cb(2, f"Step 2/6: Loading indicators for {n_candles} candles...")

    from project2_backtesting.strategy_backtester import build_multi_tf_indicators
    from shared.data_utils import normalize_timestamp

    try:
        candles = pd.read_csv(candles_path, encoding='utf-8-sig')
        print(f"[DEBUG] CSV columns: {list(candles.columns)}")

        # Auto-detect timestamp column — don't assume the name
        ts_col = None
        for col in candles.columns:
            cl = col.lower().strip()
            if cl in ('timestamp', 'time', 'date', 'datetime', 'open_time', 'open time', 'opentime'):
                ts_col = col
                break
        if ts_col is None:
            # Fallback: use first column
            ts_col = candles.columns[0]

        print(f"[DEBUG] Timestamp column detected: '{ts_col}'")

        candles['timestamp'] = pd.to_datetime(candles[ts_col], errors='coerce')
        candles = candles.dropna(subset=['timestamp'])
        candles['timestamp'] = normalize_timestamp(candles['timestamp'])
        print(f"[DEBUG] Timestamp normalized, {len(candles)} valid rows")
    except Exception as e:
        print(f"[DEBUG] FAILED at CSV loading: {e}")
        import traceback
        traceback.print_exc()
        raise

    data_dir = os.path.dirname(candles_path)

    # ── Step 2b: Ensure ALL CSV files have 'timestamp' column ─────────────
    # The backtester's _load_tf_indicators reads CSVs and expects 'timestamp'.
    # We MUST rename the column in every CSV before calling it.
    _cb(2, "Step 2/6: Standardizing CSV columns...")

    for tf in ['M5', 'M15', 'H1', 'H4', 'D1']:
        for pattern in [f'{tf}.csv', f'xauusd_{tf}.csv']:
            csv_file = os.path.join(data_dir, pattern)
            if not os.path.exists(csv_file):
                continue

            # Read first line to check columns
            with open(csv_file, 'r', encoding='utf-8-sig') as fh:
                header = fh.readline().strip()

            cols = [c.strip().strip('"').strip("'") for c in header.split(',')]
            print(f"  [P4] {tf} ({pattern}): columns = {cols[:6]}")

            if 'timestamp' not in cols:
                # Find which column is the time column
                old_name = None
                for c in cols:
                    if c.lower() in ('time', 'date', 'datetime', 'open_time', 'opentime', 'open time'):
                        old_name = c
                        break
                if old_name is None:
                    old_name = cols[0]  # assume first column

                print(f"  [P4] {tf}: renaming '{old_name}' → 'timestamp'")

                # Read entire file, rename header, write back
                with open(csv_file, 'r', encoding='utf-8-sig') as fh:
                    all_lines = fh.readlines()

                # Replace ONLY in the header line
                old_header = all_lines[0]
                new_header = old_header.replace(old_name, 'timestamp', 1)
                all_lines[0] = new_header

                with open(csv_file, 'w', encoding='utf-8', newline='') as fh:
                    fh.writelines(all_lines)

                print(f"  [P4] {tf}: DONE — header is now: {new_header.strip()[:80]}")

                # Also delete any parquet cache so it gets rebuilt with new column name
                for cache_file in os.listdir(data_dir):
                    if cache_file.startswith(f'.cache_{tf}') and cache_file.endswith('.parquet'):
                        cache_path = os.path.join(data_dir, cache_file)
                        os.remove(cache_path)
                        print(f"  [P4] Deleted stale cache: {cache_file}")
            else:
                print(f"  [P4] {tf}: already has 'timestamp' ✓")

            break  # found this TF's CSV, next TF

    # Now call build_multi_tf_indicators — all CSVs should have 'timestamp'
    _cb(2, f"Step 2/6: Building indicators for {n_candles} candles...")

    # MUST pass required_indicators to force _load_tf_indicators to use
    # compute_indicators (which sets timestamp as index) instead of
    # compute_all_indicators (which doesn't — causing KeyError: 'timestamp').
    # Passing all groups = still computes everything, but via the correct code path.
    _ALL_GROUPS = [
        'adx', 'ao', 'aroon', 'atr', 'bb', 'cci', 'dmi', 'donchian', 'dpo',
        'elder_ray', 'ema', 'fib', 'ichimoku', 'keltner', 'kst', 'macd',
        'mass_index', 'pivot', 'price_action', 'psar', 'roc', 'rsi', 'session',
        'sma', 'std_dev', 'stoch', 'supertrend', 'swing', 'tsi', 'uo',
        'volume', 'vwap', 'williams_r',
    ]
    _ALL_TF_INDICATORS = {tf: _ALL_GROUPS for tf in ['M5', 'M15', 'H1', 'H4', 'D1']}

    try:
        indicators_df = build_multi_tf_indicators(
            data_dir, candles['timestamp'],
            required_indicators=_ALL_TF_INDICATORS,
        )
        print(f"[DEBUG] Indicators built: {indicators_df.shape}")
    except Exception as e:
        print(f"[DEBUG] FAILED at build_multi_tf_indicators: {e}")
        import traceback
        traceback.print_exc()
        raise

    # ── Step 3: Compute smart features ────────────────────────────────────────
    if use_smart_features:
        _cb(3, "Step 3/6: Computing smart features...")
        try:
            from project1_reverse_engineering.smart_features import (
                _add_tf_divergences, _add_indicator_dynamics,
                _add_alignment_scores, _add_session_intelligence,
                _add_volatility_regimes, _add_price_action,
                _add_momentum_quality,
            )
            _has_smart = True
        except ImportError:
            _has_smart = False
            _cb(3, "Step 3/6: Smart features unavailable — continuing without them")

        indicators_df['hour_of_day'] = candles['timestamp'].dt.hour
        indicators_df['open_time']   = candles['timestamp'].astype(str)

        if _has_smart:
            indicators_df = _add_tf_divergences(indicators_df)
            indicators_df = _add_indicator_dynamics(indicators_df)
            indicators_df = _add_alignment_scores(indicators_df)
            indicators_df = _add_session_intelligence(indicators_df)
            indicators_df = _add_volatility_regimes(indicators_df)
            indicators_df = _add_price_action(indicators_df)
            indicators_df = _add_momentum_quality(indicators_df)
    else:
        _cb(3, "Step 3/6: Skipping smart features")

    # ── Step 4: Merge labels with indicators ──────────────────────────────────
    _cb(4, "Step 4/6: Merging data...")

    labels_df['timestamp']    = pd.to_datetime(labels_df['timestamp'])
    indicators_df['timestamp'] = candles['timestamp'].values

    merged = labels_df.merge(indicators_df, on='timestamp', how='inner')

    meta_cols = {'timestamp', 'direction', 'label', 'pips_result',
                 'hold_candles', 'exit_reason', 'hour_of_day', 'open_time'}
    feature_cols = [c for c in merged.columns if c not in meta_cols]

    # Drop columns >90% NaN
    valid_cols = [c for c in feature_cols if merged[c].notna().mean() > 0.1]

    X    = merged[valid_cols].fillna(0)
    y    = merged['label'].values
    pips = merged['pips_result'].values

    n_original = len([c for c in valid_cols if not c.startswith('SMART_')])
    n_smart    = len([c for c in valid_cols if c.startswith('SMART_')])

    min_coverage = max(10, int(len(X) * min_coverage_pct / 100))

    try:
        from xgboost import XGBClassifier
    except ImportError:
        raise ImportError("XGBoost not installed. Run: pip install xgboost")

    # ── Steps 5-6: Discovery — mode determines the search strategy ────────────
    # WHY: Quick uses greedy search (fast, may miss combos). Deep tests all combos
    #      of top features. Exhaustive uses genetic search on ALL features.
    # CHANGED: April 2026 — three discovery modes + enhancement flags
    # Enhancement flags — passed to whichever discovery mode is selected.
    # Each implementation is added by its own prompt (Level 1-4).
    # WHY: Stubs here so the code doesn't break before enhancements are implemented.
    enhance_opts = {
        'grid_threshold':       enhance_grid_threshold,
        'multi_exit':           enhance_multi_exit,
        'walkforward_score':    enhance_walkforward_score,
        'feature_interactions': enhance_feature_interactions,
    }

    if discovery_mode == 'deep':
        final_rules, model_metrics = _discover_deep(
            X, y, pips, merged, valid_cols,
            n_estimators=n_estimators, max_depth=max_depth,
            min_coverage=min_coverage, min_win_rate=min_win_rate,
            max_rules=max_rules, train_test_split=train_test_split,
            progress_callback=_cb,
            enhancements=enhance_opts,
        )
    elif discovery_mode == 'exhaustive':
        final_rules, model_metrics = _discover_exhaustive(
            X, y, pips, merged, valid_cols,
            n_estimators=n_estimators, max_depth=max_depth,
            min_coverage=min_coverage, min_win_rate=min_win_rate,
            max_rules=max_rules, train_test_split=train_test_split,
            progress_callback=_cb,
            enhancements=enhance_opts,
        )
    else:
        # Quick mode — current behavior (unchanged)
        final_rules, model_metrics = _discover_quick(
            X, y, pips, merged, valid_cols,
            n_estimators=n_estimators, max_depth=max_depth,
            min_coverage=min_coverage, min_win_rate=min_win_rate,
            max_rules=max_rules, train_test_split=train_test_split,
            progress_callback=_cb,
            enhancements=enhance_opts,
        )

    elapsed = time.time() - start

    _cb(total_steps, total_steps,
        f"Done! {len(final_rules)} rules from {n_candles} candles in {elapsed:.0f}s")

    model_metrics['n_estimators'] = n_estimators
    model_metrics['max_depth'] = max_depth

    result = {
        "method":             "scratch_xgboost",
        "generated_at":       datetime.now().isoformat(),
        "computation_time_s": round(elapsed, 1),
        "candles_analyzed":   n_candles,
        "base_win_rate":      round(win_rate_base, 3),
        "entry_timeframe":    entry_timeframe or 'H1',
        "sl_pips":            sl_pips,
        "tp_pips":            tp_pips,
        "direction":          direction,
        "max_hold_candles":   max_hold_candles,
        "spread_pips":        spread_pips,
        "features_used":      len(valid_cols),
        "original_features":  n_original,
        "smart_features":     n_smart,
        "discovery_mode":     discovery_mode,
        "enhancements_used":  {k: v for k, v in enhance_opts.items() if v},
        "rules":              final_rules,
        "model_metrics":      model_metrics,
        "profile": {
            "method":           "Scratch Discovery (no robot needed)",
            "candles_analyzed": n_candles,
            "feature_count":    len(valid_cols),
        },
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(RESULT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_scratch_result():
    """Load cached scratch result (or None)."""
    if os.path.exists(RESULT_PATH):
        with open(RESULT_PATH, encoding='utf-8') as f:
            return json.load(f)
    return None


def activate_scratch_rules():
    """
    Copy scratch-discovered rules into analysis_report.json so the
    existing backtester / refiner / validator pick them up automatically.
    Original file is backed up first.
    """
    import shutil

    p1_outputs  = os.path.join(os.path.dirname(_HERE),
                               'project1_reverse_engineering', 'outputs')
    report_path = os.path.join(p1_outputs, 'analysis_report.json')
    backup_path = os.path.join(p1_outputs, 'analysis_report_before_scratch.json')

    scratch = load_scratch_result()
    if scratch is None:
        raise FileNotFoundError("No scratch results found. Run discovery first.")

    os.makedirs(p1_outputs, exist_ok=True)

    if os.path.exists(report_path) and not os.path.exists(backup_path):
        shutil.copy2(report_path, backup_path)

    if os.path.exists(report_path):
        with open(report_path, encoding='utf-8') as f:
            current = json.load(f)
    else:
        current = {}

    current['rules'] = scratch['rules']
    # WHY: P2 reads entry_timeframe from this file to load the correct candle CSV.
    #      Without this, everything defaults to H1 even if rules were found on M15.
    # CHANGED: April 2026 — entry TF travels with the rules
    current['entry_timeframe'] = scratch.get('entry_timeframe', 'H1')
    current['feature_importance'] = {
        'top_20':         scratch['model_metrics'].get('feature_importance_top_20', []),
        'train_accuracy': scratch['model_metrics']['train_accuracy'],
        'test_accuracy':  scratch['model_metrics']['test_accuracy'],
    }
    current['discovery_method'] = 'scratch_xgboost'

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(current, f, indent=2, default=str)

    return len(scratch['rules'])


def restore_previous_rules():
    """Restore analysis_report.json from the backup created by activate_scratch_rules."""
    import shutil

    p1_outputs  = os.path.join(os.path.dirname(_HERE),
                               'project1_reverse_engineering', 'outputs')
    report_path = os.path.join(p1_outputs, 'analysis_report.json')
    backup_path = os.path.join(p1_outputs, 'analysis_report_before_scratch.json')

    if not os.path.exists(backup_path):
        raise FileNotFoundError("No backup found. Activate scratch rules first.")

    shutil.copy2(backup_path, report_path)
    os.remove(backup_path)


def _discover_quick(X, y, pips, merged, valid_cols,
                    n_estimators=300, max_depth=4, min_coverage=100,
                    min_win_rate=0.55, max_rules=25, train_test_split=0.7,
                    progress_callback=None, enhancements=None):
    """
    QUICK MODE: Current approach — XGBoost → top 30 → decision tree → rules.
    Fast (~5 min) but greedy — can miss combinations that work together.
    """
    if enhancements is None:
        enhancements = {}
    active = [k for k, v in enhancements.items() if v]
    if active:
        print(f"[DISCOVERY quick] Enhancements enabled: {', '.join(active)}")
    # Enhancement implementations added by Level 1-4 prompts

    def _cb(step, msg):
        if progress_callback:
            progress_callback(step, 6, msg)

    _cb(5, f"[Quick] Training XGBoost on {len(X)} rows x {len(valid_cols)} features...")

    from xgboost import XGBClassifier
    from sklearn.tree import DecisionTreeClassifier

    split_idx = int(len(X) * train_test_split)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    model = XGBClassifier(
        n_estimators=n_estimators, max_depth=max_depth,
        learning_rate=0.05, subsample=0.8, colsample_bytree=0.7,
        min_child_weight=min_coverage, reg_alpha=0.1, reg_lambda=1.0,
        random_state=42, eval_metric='logloss', n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    train_acc = model.score(X_train, y_train)
    test_acc = model.score(X_test, y_test)

    importances = model.feature_importances_
    top_indices = np.argsort(importances)[::-1]
    top_features = [(valid_cols[i], float(importances[i])) for i in top_indices[:50]]

    _cb(6, "[Quick] Extracting rules from decision tree...")

    top_feat_names = [f[0] for f in top_features[:30]]
    X_top = X[top_feat_names]

    all_rules = []
    for depth in [3, 4, 5]:
        tree = DecisionTreeClassifier(
            max_depth=depth, min_samples_leaf=min_coverage,
            random_state=42 + depth,
        )
        tree.fit(X_top, y)
        rules = _extract_rules(tree, top_feat_names, X_top, y, pips, merged,
                               max_rules=10, min_coverage=min_coverage)
        all_rules.extend(rules)

    unique = _deduplicate(all_rules)
    quality = [r for r in unique if r['win_rate'] >= min_win_rate and r['prediction'] == 'WIN']
    for r in quality:
        r['score'] = r['win_rate'] * np.sqrt(r['coverage']) * max(1 + r['avg_pips'] / 200, 0.1)
    quality.sort(key=lambda r: r['score'], reverse=True)

    model_metrics = {
        'train_accuracy': round(train_acc, 4),
        'test_accuracy': round(test_acc, 4),
        'feature_importance_top_20': top_features[:20],
        'discovery_mode': 'quick',
    }

    return quality[:max_rules], model_metrics


def _discover_deep(X, y, pips, merged, valid_cols,
                   n_estimators=300, max_depth=4, min_coverage=100,
                   min_win_rate=0.55, max_rules=25, train_test_split=0.7,
                   progress_callback=None, enhancements=None):
    """
    DEEP MODE: Run XGBoost 10 times with different random subsets → union top features
    → test ALL combinations of those features exhaustively.

    WHY: Quick mode picks features greedily. Deep mode ensures every combination of
    the top features is tested, finding combos that only work together.

    Process:
    1. Train XGBoost 10 times with colsample_bytree=0.3 and different seeds
    2. Collect feature importances from all 10 runs
    3. Union the top features → pool of ~50 best features
    4. For each combination of `max_depth` features from that pool:
       - Fit a small decision tree on just those features
       - Extract the best rule from that tree
    5. Score, deduplicate, return best rules

    ~20-30 minutes for 50 features at depth 4 (230K combinations)
    """
    if enhancements is None:
        enhancements = {}
    active = [k for k, v in enhancements.items() if v]
    if active:
        print(f"[DISCOVERY deep] Enhancements enabled: {', '.join(active)}")
    # Enhancement implementations added by Level 1-4 prompts

    from xgboost import XGBClassifier
    from sklearn.tree import DecisionTreeClassifier
    from itertools import combinations

    def _cb(step, total, msg):
        if progress_callback:
            progress_callback(step, total, msg)

    total_steps = 12  # 10 xgb runs + combo search + scoring

    split_idx = int(len(X) * train_test_split)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    # ── Phase 1: Multi-run XGBoost to discover diverse features ───────────
    all_importances = {}  # feature_name → max importance across runs
    n_runs = 10
    model = None

    for run_i in range(n_runs):
        _cb(run_i + 1, total_steps,
            f"[Deep] XGBoost run {run_i+1}/{n_runs} (colsample=0.3, seed={42+run_i})...")

        model = XGBClassifier(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=0.05, subsample=0.8,
            colsample_bytree=0.3,  # Each run sees only 30% of features
            min_child_weight=min_coverage, reg_alpha=0.1, reg_lambda=1.0,
            random_state=42 + run_i, eval_metric='logloss', n_jobs=-1,
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

        for idx, imp in enumerate(model.feature_importances_):
            feat = valid_cols[idx]
            if imp > 0:
                all_importances[feat] = max(all_importances.get(feat, 0), float(imp))

    # ── Phase 2: Select top features from union of all runs ───────────────
    sorted_feats = sorted(all_importances.items(), key=lambda x: x[1], reverse=True)
    n_top = min(50, len(sorted_feats))  # Top 50 features from all runs
    top_feat_names = [f[0] for f in sorted_feats[:n_top]]

    _cb(n_runs + 1, total_steps,
        f"[Deep] Found {len(all_importances)} useful features across {n_runs} runs. "
        f"Testing all combos of top {n_top}...")

    # ── Phase 3: Test ALL combinations of top features ────────────────────
    # For depth=4 with 50 features: C(50,4) = 230,300 combos
    X_pool = X[top_feat_names]
    all_rules = []
    combo_list = list(combinations(range(n_top), max_depth))
    total_combos = len(combo_list)

    _cb(n_runs + 1, total_steps,
        f"[Deep] Testing {total_combos:,} combinations of {max_depth} features...")

    batch_size = max(1, total_combos // 20)  # Report progress ~20 times

    for ci, combo in enumerate(combo_list):
        if ci % batch_size == 0:
            pct = ci / total_combos * 100
            _cb(n_runs + 1, total_steps,
                f"[Deep] Combo {ci:,}/{total_combos:,} ({pct:.0f}%)...")

        feat_names = [top_feat_names[i] for i in combo]
        X_combo = X_pool[feat_names]

        try:
            tree = DecisionTreeClassifier(
                max_depth=max_depth, min_samples_leaf=min_coverage,
                random_state=42,
            )
            tree.fit(X_combo, y)
            rules = _extract_rules(tree, feat_names, X_combo, y, pips, merged,
                                   max_rules=2, min_coverage=min_coverage)
            all_rules.extend(rules)
        except Exception:
            continue

    # ── Phase 4: Score and deduplicate ────────────────────────────────────
    _cb(n_runs + 2, total_steps, f"[Deep] Scoring {len(all_rules)} candidate rules...")

    unique = _deduplicate(all_rules)
    quality = [r for r in unique if r['win_rate'] >= min_win_rate and r['prediction'] == 'WIN']
    for r in quality:
        r['score'] = r['win_rate'] * np.sqrt(r['coverage']) * max(1 + r['avg_pips'] / 200, 0.1)
    quality.sort(key=lambda r: r['score'], reverse=True)

    test_acc = model.score(X_test, y_test) if model else 0
    train_acc = model.score(X_train, y_train) if model else 0

    model_metrics = {
        'train_accuracy': round(train_acc, 4),
        'test_accuracy': round(test_acc, 4),
        'feature_importance_top_20': sorted_feats[:20],
        'discovery_mode': 'deep',
        'xgb_runs': n_runs,
        'features_pool': n_top,
        'combos_tested': total_combos,
        'candidates_found': len(all_rules),
    }

    return quality[:max_rules], model_metrics


def _discover_exhaustive(X, y, pips, merged, valid_cols,
                         n_estimators=300, max_depth=4, min_coverage=100,
                         min_win_rate=0.55, max_rules=25, train_test_split=0.7,
                         progress_callback=None, enhancements=None):
    """
    EXHAUSTIVE MODE: Genetic algorithm that searches ALL features (not just top 50).

    WHY: Deep mode tests all combos of the top 50 features. But what if the best
    combination includes feature #200 that XGBoost ranked as unimportant? Genetic
    search explores the FULL feature space by evolving combinations over generations.

    Process:
    1. Create 500 random combinations of `max_depth` features from ALL valid features
    2. Score each combination (fit small tree, extract best rule, compute score)
    3. Keep the top 100 (selection)
    4. Create new combos by mixing features from two good parents (crossover)
    5. Randomly swap in features from the full pool (mutation)
    6. Repeat for 100 generations
    7. Final generation → extract and return best rules

    ~1-2 hours for 670 features × 100 generations × 500 population
    """
    if enhancements is None:
        enhancements = {}
    active = [k for k, v in enhancements.items() if v]
    if active:
        print(f"[DISCOVERY exhaustive] Enhancements enabled: {', '.join(active)}")
    # Enhancement implementations added by Level 1-4 prompts

    from sklearn.tree import DecisionTreeClassifier
    import random as rng

    def _cb(step, total, msg):
        if progress_callback:
            progress_callback(step, total, msg)

    rng.seed(42)
    n_features = len(valid_cols)
    population_size = 500
    n_generations = 100
    n_keep = 100          # Top survivors per generation
    mutation_rate = 0.2   # Chance of swapping one feature for a random one
    crossover_rate = 0.6  # Chance of breeding two parents
    total_steps = n_generations + 2

    # Baseline XGBoost for metrics and seeding
    _cb(1, total_steps, f"[Exhaustive] Baseline XGBoost on {n_features} features...")

    from xgboost import XGBClassifier
    split_idx = int(len(X) * train_test_split)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    baseline_model = XGBClassifier(
        n_estimators=n_estimators, max_depth=max_depth,
        learning_rate=0.05, subsample=0.8, colsample_bytree=0.7,
        min_child_weight=min_coverage, random_state=42,
        eval_metric='logloss', n_jobs=-1,
    )
    baseline_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    train_acc = baseline_model.score(X_train, y_train)
    test_acc = baseline_model.score(X_test, y_test)

    importances = baseline_model.feature_importances_
    top_indices = np.argsort(importances)[::-1][:50]

    def _score_combo(feature_indices):
        """Score a feature combination: fit tree, extract best rule, return score."""
        feat_names = [valid_cols[i] for i in feature_indices]
        X_combo = X[feat_names]
        try:
            tree = DecisionTreeClassifier(
                max_depth=max_depth, min_samples_leaf=min_coverage,
                random_state=42,
            )
            tree.fit(X_combo, y)
            rules = _extract_rules(tree, feat_names, X_combo, y, pips, merged,
                                   max_rules=1, min_coverage=min_coverage)
            if rules and rules[0]['prediction'] == 'WIN':
                r = rules[0]
                return r['win_rate'] * np.sqrt(r['coverage']) * max(1 + r['avg_pips'] / 200, 0.1), r
            return 0.0, None
        except Exception:
            return 0.0, None

    # ── Phase 1: Initialize population ────────────────────────────────────
    _cb(2, total_steps, f"[Exhaustive] Creating initial population of {population_size}...")

    population = []
    # 20% seeded from XGBoost top features
    n_seeded = population_size // 5
    for _ in range(n_seeded):
        combo = tuple(rng.sample(list(top_indices), min(max_depth, len(top_indices))))
        if len(combo) == max_depth:
            population.append(combo)

    # 80% random from all features
    while len(population) < population_size:
        combo = tuple(rng.sample(range(n_features), max_depth))
        population.append(combo)

    # ── Phase 2: Evolution ────────────────────────────────────────────────
    best_ever_score = 0.0
    all_good_rules = []

    for gen in range(n_generations):
        _cb(gen + 2, total_steps,
            f"[Exhaustive] Generation {gen+1}/{n_generations} — "
            f"pop: {len(population)}, best score: {best_ever_score:.1f}...")

        # Score all combos
        scored = []
        for combo in population:
            score, rule = _score_combo(combo)
            scored.append((score, combo, rule))

            if score > best_ever_score:
                best_ever_score = score

            if rule and rule['win_rate'] >= min_win_rate:
                all_good_rules.append(rule)

        # Sort by score
        scored.sort(key=lambda x: x[0], reverse=True)

        # Keep top N (selection)
        survivors = [s[1] for s in scored[:n_keep]]

        # Build next generation
        next_gen = list(survivors)  # Elitism: keep all survivors

        while len(next_gen) < population_size:
            if rng.random() < crossover_rate and len(survivors) >= 2:
                # Crossover: pick 2 parents, mix their features
                p1, p2 = rng.sample(survivors, 2)
                child = []
                for i in range(max_depth):
                    child.append(p1[i] if rng.random() < 0.5 else p2[i])
                # Ensure no duplicates
                child = list(set(child))
                while len(child) < max_depth:
                    child.append(rng.randint(0, n_features - 1))
                next_gen.append(tuple(child[:max_depth]))
            else:
                # Random new combo (exploration)
                next_gen.append(tuple(rng.sample(range(n_features), max_depth)))

        # Mutation: randomly swap one feature
        for i in range(n_keep, len(next_gen)):
            if rng.random() < mutation_rate:
                combo = list(next_gen[i])
                idx_to_replace = rng.randint(0, max_depth - 1)
                combo[idx_to_replace] = rng.randint(0, n_features - 1)
                next_gen[i] = tuple(combo)

        population = next_gen[:population_size]

    # ── Phase 3: Final scoring and deduplication ──────────────────────────
    _cb(n_generations + 2, total_steps,
        f"[Exhaustive] Scoring {len(all_good_rules)} candidate rules from {n_generations} generations...")

    unique = _deduplicate(all_good_rules)
    quality = [r for r in unique if r['win_rate'] >= min_win_rate and r['prediction'] == 'WIN']
    for r in quality:
        r['score'] = r['win_rate'] * np.sqrt(r['coverage']) * max(1 + r['avg_pips'] / 200, 0.1)
    quality.sort(key=lambda r: r['score'], reverse=True)

    model_metrics = {
        'train_accuracy': round(train_acc, 4),
        'test_accuracy': round(test_acc, 4),
        'feature_importance_top_20': [(valid_cols[i], float(importances[i]))
                                      for i in top_indices[:20]],
        'discovery_mode': 'exhaustive',
        'generations': n_generations,
        'population_size': population_size,
        'total_features': n_features,
        'candidates_found': len(all_good_rules),
        'best_score': round(best_ever_score, 2),
    }

    return quality[:max_rules], model_metrics


def _extract_rules(tree, feature_names, X, y, pips, df, max_rules=10, min_coverage=100):
    """Extract rules from a fitted DecisionTreeClassifier."""
    from sklearn.tree import _tree

    tree_  = tree.tree_
    rules  = []

    def _recurse(node, conditions):
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            samples = int(tree_.n_node_samples[node])
            if samples < min_coverage:
                return
            value = tree_.value[node][0]
            total = value.sum()
            if total == 0:
                return
            win_count  = value[1] if len(value) > 1 else 0
            confidence = max(value) / total
            if confidence < 0.55:
                return
            prediction = "WIN" if win_count > (total - win_count) else "LOSS"

            mask           = np.ones(len(X), dtype=bool)
            rule_conditions = []
            for feat_idx, op, threshold in conditions:
                feat_name = feature_names[feat_idx]
                col       = X[feat_name].values
                if op == "<=":
                    mask &= col <= threshold
                else:
                    mask &= col > threshold
                rule_conditions.append({
                    "feature":  feat_name,
                    "operator": op,
                    "value":    round(float(threshold), 4),
                })

            if mask.sum() < min_coverage:
                return

            win_rate = float(y[mask].mean())
            avg_p    = float(pips[mask].mean())

            rules.append({
                "conditions":   rule_conditions,
                "prediction":   prediction,
                "confidence":   round(confidence, 3),
                "coverage":     int(mask.sum()),
                "coverage_pct": round(mask.sum() / len(df) * 100, 1),
                "win_rate":     round(win_rate, 3),
                "avg_pips":     round(avg_p, 1),
            })
            return

        feat      = tree_.feature[node]
        threshold = tree_.threshold[node]
        _recurse(tree_.children_left[node],  conditions + [(feat, "<=", threshold)])
        _recurse(tree_.children_right[node], conditions + [(feat, ">",  threshold)])

    _recurse(0, [])
    rules.sort(key=lambda r: r['win_rate'] * np.sqrt(r['coverage']), reverse=True)
    return rules[:max_rules]


def _deduplicate(rules, threshold=0.7):
    unique = []
    for rule in rules:
        sig    = set(f"{c['feature']}_{c['operator']}" for c in rule['conditions'])
        is_dup = False
        for existing in unique:
            esig = set(f"{c['feature']}_{c['operator']}" for c in existing['conditions'])
            overlap = len(sig & esig) / max(len(sig | esig), 1)
            if overlap > threshold:
                if rule['win_rate'] > existing['win_rate']:
                    unique.remove(existing)
                    unique.append(rule)
                is_dup = True
                break
        if not is_dup:
            unique.append(rule)
    return unique

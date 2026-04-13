"""
Bot Entry Rule Discovery — Phase A.25

Reverse-engineers the bot's actual entry rules by training XGBoost on a
candle-level dataset where the label is "did the bot open a trade on this
candle". Distinct from xgboost_discovery.py which finds profitable patterns
among existing trades — this finds the trigger conditions the bot uses.

WHY: The user wants to validate that the bot is doing what they think it's
     doing. Running this against the bot's trade history reveals the actual
     decision rule, which can then be compared against the assumed rule.
     The discovered rules are also testable in Project 2 like any normal
     rule source.

CHANGED: April 2026 — Phase A.25
"""

import os
import json
import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')
BOT_RULES_PATH = os.path.join(OUTPUT_DIR, 'bot_entry_rules.json')

# ── Discovery hyperparameters ─────────────────────────────────────────────────
TIMEFRAMES = ['M5', 'M15', 'H1', 'H4', 'D1']
NEG_RATIO  = 10  # 10 negative candles per positive candle
RANDOM_SEED = 42


def _log(msg, cb):
    if cb:
        cb(msg)


def _align_trades_to_tf(trades_df, tf):
    """Bucket each trade's open_time into the candle of the requested TF.

    Returns a set of pandas Timestamps (one per unique candle that contains
    at least one trade).

    WHY: Multiple trades in the same H1 candle should collapse to one
         positive H1 row — the bot took at least one entry there, that's
         the label. The exact count doesn't matter for binary classification.
    CHANGED: April 2026 — Phase A.25
    """
    tf_to_freq = {
        'M5':  '5min',
        'M15': '15min',
        'H1':  '1h',
        'H4':  '4h',
        'D1':  '1D',
    }
    freq = tf_to_freq[tf]
    ts = pd.to_datetime(trades_df['open_time'], errors='coerce').dropna()
    bucketed = ts.dt.floor(freq)
    return set(bucketed.unique())


def _build_candle_matrix_for_tf(tf, data_dir, trade_candle_set, progress_cb):
    """Build the candle-level X/y matrix for one timeframe.

    Loads raw candles for the TF, computes multi-TF indicators (M5..D1)
    aligned to this TF's spine, samples 10× random negatives per positive
    stratified by calendar quarter, and returns (X, y, feature_cols).

    WHY: The discovery model needs a balanced, representative sample. Pure
         random sampling could overweight 2025-2026 candles where most
         positives live, leaking time-of-data into the prediction. Stratify
         per quarter so each quarter's negative sample size is proportional
         to its positive count.
    CHANGED: April 2026 — Phase A.25
    """
    import sys as _sys
    _proj_root = os.path.abspath(os.path.join(_HERE, '..'))
    if _proj_root not in _sys.path:
        _sys.path.insert(0, _proj_root)

    from project2_backtesting.strategy_backtester import (
        _load_tf_indicators, build_multi_tf_indicators,
    )

    _log(f"  [{tf}] loading candles + indicators...", progress_cb)
    tf_ind = _load_tf_indicators(tf, data_dir, needed_indicators=None)
    if tf_ind is None or len(tf_ind) == 0:
        _log(f"  [{tf}] no candles found, skipping", progress_cb)
        return None, None, None

    # Indicators are already prefixed (M5_..., H1_..., etc.) per TF.
    # Build full multi-TF indicators on this TF's timestamp spine.
    _log(f"  [{tf}] merging cross-TF indicators...", progress_cb)
    spine = tf_ind['timestamp']
    full_ind = build_multi_tf_indicators(data_dir, spine, required_indicators=None)
    full_ind['timestamp'] = spine.values

    # Label: 1 if this candle's timestamp is in the trade-bucket set for this TF
    full_ind['label'] = full_ind['timestamp'].isin(trade_candle_set).astype(int)
    n_pos = int(full_ind['label'].sum())
    n_total = len(full_ind)
    _log(f"  [{tf}] {n_pos} positive candles out of {n_total}", progress_cb)

    if n_pos < 30:
        _log(f"  [{tf}] too few positives ({n_pos}) — need at least 30. Skipping.",
             progress_cb)
        return None, None, None

    # Stratified negative sampling per quarter
    full_ind['quarter'] = pd.to_datetime(full_ind['timestamp']).dt.to_period('Q').astype(str)

    rng = np.random.default_rng(RANDOM_SEED)
    sampled_indices = []
    sampled_indices.extend(full_ind.index[full_ind['label'] == 1].tolist())

    for q, q_group in full_ind.groupby('quarter'):
        n_pos_q = int((q_group['label'] == 1).sum())
        if n_pos_q == 0:
            continue
        n_neg_target = n_pos_q * NEG_RATIO
        neg_pool = q_group.index[q_group['label'] == 0].tolist()
        if len(neg_pool) == 0:
            continue
        n_neg_take = min(n_neg_target, len(neg_pool))
        sampled_indices.extend(rng.choice(neg_pool, size=n_neg_take, replace=False).tolist())

    matrix = full_ind.loc[sorted(set(sampled_indices))].copy()
    matrix = matrix.drop(columns=['quarter'])

    # Drop non-feature columns
    EXCLUDE = {'timestamp', 'label'}
    feature_cols = [c for c in matrix.columns if c not in EXCLUDE]

    X = matrix[feature_cols].fillna(0).astype('float32')
    y = matrix['label'].astype(int)

    _log(f"  [{tf}] sampled matrix: {len(X)} rows ({int(y.sum())} pos / {len(y) - int(y.sum())} neg), {len(feature_cols)} features",
         progress_cb)
    return X, y, feature_cols


def discover_bot_entry_rules(
    max_rules=25,
    max_depth=4,
    n_estimators=200,
    min_coverage=20,
    min_win_rate=0.55,
    progress_callback=None,
):
    """Run bot-entry discovery across all timeframes and write the merged result.

    Returns dict with keys: status, rules, per_tf_summary.

    WHY: Multi-TF discovery in one pass — finds both fast triggers (M5)
         and slow context filters (H4/D1). Each rule is tagged with the
         TF it was discovered on, so users can see whether the bot is a
         scalper or a swing system.
    CHANGED: April 2026 — Phase A.25
    """
    cb = progress_callback
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        from xgboost import XGBClassifier
    except ImportError:
        raise ImportError(
            "xgboost not installed. Run: pip install xgboost"
        )

    from sklearn.tree import DecisionTreeClassifier
    from project1_reverse_engineering.xgboost_discovery import (
        _extract_xgboost_leaf_rules, _dedupe_rules, fill_feature_nans,
    )

    # Load the bot's trades — try both feature_matrix.csv (which has open_time)
    # and aligned_trades.csv as a fallback.
    _log("Loading trade history...", cb)
    fm_path = os.path.join(OUTPUT_DIR, 'feature_matrix.csv')
    if not os.path.exists(fm_path):
        raise FileNotFoundError(
            f"{fm_path} not found. Run Project 1 → Run Scenarios first."
        )
    trades_df = pd.read_csv(fm_path, usecols=['open_time'])
    n_trades = len(trades_df)
    _log(f"  Loaded {n_trades} trades", cb)

    # Resolve candle data dir
    project_root = os.path.abspath(os.path.join(_HERE, '..'))
    data_dir = os.path.join(project_root, 'data')

    all_rules = []
    per_tf_summary = []

    for tf_idx, tf in enumerate(TIMEFRAMES):
        _log(f"[{tf_idx + 1}/{len(TIMEFRAMES)}] Processing {tf}...", cb)
        trade_candle_set = _align_trades_to_tf(trades_df, tf)

        try:
            X, y, feature_cols = _build_candle_matrix_for_tf(
                tf, data_dir, trade_candle_set, cb
            )
        except Exception as e:
            _log(f"  [{tf}] failed: {type(e).__name__}: {e}", cb)
            per_tf_summary.append({'tf': tf, 'status': 'failed', 'error': str(e)})
            continue

        if X is None:
            per_tf_summary.append({'tf': tf, 'status': 'skipped'})
            continue

        # 70/30 chronological train/test
        split = int(len(X) * 0.7)
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]

        # Train XGB just to inform the DT (same pattern as run_xgboost_discovery)
        _log(f"  [{tf}] training XGBoost...", cb)
        xgb = XGBClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=0.08,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=min_coverage,
            random_state=RANDOM_SEED,
            n_jobs=-1,
            verbosity=0,
            eval_metric='logloss',
            scale_pos_weight=(len(y_train) - y_train.sum()) / max(y_train.sum(), 1),
        )
        xgb.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

        # Extract rules via the existing decision-tree helper
        _log(f"  [{tf}] extracting rules...", cb)
        tf_rules = _extract_xgboost_leaf_rules(
            xgb.get_booster(), X_train, y_train, X_test, y_test,
            feature_cols, max_rules, max_depth, min_coverage, min_win_rate,
        )

        # Tag each rule with its TF and source
        for r in tf_rules:
            r['entry_timeframe'] = tf
            r['source']          = 'bot_entry'

        _log(f"  [{tf}] kept {len(tf_rules)} rules", cb)
        per_tf_summary.append({
            'tf':           tf,
            'status':       'ok',
            'n_rules':      len(tf_rules),
            'n_train':      int(len(X_train)),
            'n_test':       int(len(X_test)),
            'n_features':   int(len(feature_cols)),
        })
        all_rules.extend(tf_rules)

    # Deduplicate across TFs
    _log(f"Total rules across all TFs: {len(all_rules)}", cb)
    deduped = _dedupe_rules(all_rules)
    _log(f"After dedup: {len(deduped)}", cb)

    result = {
        'status':           'ok',
        'discovery_method': 'bot_entry_v1',
        'n_trades':         n_trades,
        'rules':            deduped,
        'per_tf_summary':   per_tf_summary,
        'params': {
            'max_rules':    max_rules,
            'max_depth':    max_depth,
            'n_estimators': n_estimators,
            'min_coverage': min_coverage,
            'min_win_rate': min_win_rate,
            'neg_ratio':    NEG_RATIO,
        },
    }

    with open(BOT_RULES_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    _log(f"Saved: {BOT_RULES_PATH}", cb)

    return result

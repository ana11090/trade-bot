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
    # WHY (Phase A.34): Import line referenced two names that do not
    #      exist in xgboost_discovery.py. `_dedupe_rules` was a typo —
    #      the real function is `_deduplicate_rules` (at line 322 of
    #      xgboost_discovery.py). `fill_feature_nans` never existed in
    #      that module at all and was never called here anyway — dead
    #      import, safe to remove. Both names caused the whole import
    #      to fail with ImportError, killing Step 4 on every scenario.
    # CHANGED: April 2026 — Phase A.34
    from project1_reverse_engineering.xgboost_discovery import (
        _extract_xgboost_leaf_rules,
        _deduplicate_rules,
    )

    # Load the bot's trades — try both feature_matrix.csv (which has open_time)
    # and aligned_trades.csv as a fallback.
    _log("Loading trade history...", cb)
    fm_path = os.path.join(OUTPUT_DIR, 'feature_matrix.csv')
    if not os.path.exists(fm_path):
        raise FileNotFoundError(
            f"{fm_path} not found. Run Project 1 → Run Scenarios first."
        )
    # WHY (Phase A.31): Old code only loaded `open_time`. Per-rule action
    #      tagging needs the action column too so we can compute the
    #      BUY/SELL split among trades that fall inside each rule's
    #      matching candles. Also load `pips` so we can compute a real
    #      avg_pips per rule (the bot_entry rule extractor leaves it
    #      at 0.0 because the candle dataset has no pip outcomes —
    #      we patch it below from the trade dataset).
    # CHANGED: April 2026 — Phase A.31
    _a31_cols = ['open_time']
    try:
        _a31_header = pd.read_csv(fm_path, nrows=0).columns.tolist()
    except Exception:
        _a31_header = []
    if 'action' in _a31_header:
        _a31_cols.append('action')
    if 'pips' in _a31_header:
        _a31_cols.append('pips')
    trades_df = pd.read_csv(fm_path, usecols=_a31_cols)
    if 'action' not in trades_df.columns:
        trades_df['action'] = 'BUY'  # safest default — flag it below
        _log(
            "  WARNING: feature_matrix.csv has no 'action' column. "
            "All rules will be tagged action='BUY'. To get directional rules, "
            "ensure your trade history exports the trade direction.",
            cb,
        )
    n_trades = len(trades_df)
    _log(f"  Loaded {n_trades} trades", cb)

    # Pre-compute per-TF trade timestamp buckets keyed by direction. We
    # need this inside the per-TF loop below to tag rules with action.
    # WHY (Phase A.31): Avoids re-bucketing trades inside the rule loop.
    # CHANGED: April 2026 — Phase A.31
    _a31_action_norm = trades_df['action'].astype(str).str.upper().str.strip()
    _a31_buy_mask  = _a31_action_norm.str.contains('BUY')  | (_a31_action_norm == 'LONG')
    _a31_sell_mask = _a31_action_norm.str.contains('SELL') | (_a31_action_norm == 'SHORT')
    _a31_pips_per_trade = (
        trades_df['pips'].astype(float)
        if 'pips' in trades_df.columns
        else pd.Series([0.0] * len(trades_df))
    )

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

        # WHY (Phase A.31): Tag each rule with its TF + source AND with
        #      a per-rule `action` field derived from the bot's actual
        #      trade history. For each rule, find the candle timestamps
        #      where the rule fires, look up the trades whose open_time
        #      buckets into those candles, and compute the BUY/SELL
        #      split. 60% majority → directional; otherwise BOTH. This
        #      matches the threshold step6_extract_rules.py uses for
        #      bot-level direction. Also computes avg_pips from the
        #      same matching trades so the rule has a real expectancy
        #      number instead of the 0.0 placeholder.
        #      With per-rule action set, A.30's direction expansion in
        #      run_comparison_matrix will route directional rules to
        #      the correct side and split BOTH rules into two combos
        #      so the user can see which direction wins.
        # CHANGED: April 2026 — Phase A.31
        _A31_DIR_THRESHOLD = 0.60

        # Per-TF bucketing of trade timestamps to this TF's grain
        _tf_to_freq = {'M5': '5min', 'M15': '15min', 'H1': '1h', 'H4': '4h', 'D1': '1D'}
        _a31_trade_ts = pd.to_datetime(
            trades_df['open_time'], errors='coerce'
        )
        _a31_trade_bucket = _a31_trade_ts.dt.floor(_tf_to_freq[tf])

        # Build a frame indexed by trade row to look up direction + pips
        _a31_trade_lookup = pd.DataFrame({
            'bucket': _a31_trade_bucket,
            'is_buy':  _a31_buy_mask.values,
            'is_sell': _a31_sell_mask.values,
            'pips':    _a31_pips_per_trade.values,
        }).dropna(subset=['bucket'])

        for r in tf_rules:
            r['entry_timeframe'] = tf
            r['source']          = 'bot_entry'

            # Re-evaluate this rule's mask against the same X frame the
            # tree was extracted from (which already has the post-sample
            # candle universe — so this is fast).
            try:
                _r_mask = pd.Series(True, index=X.index)
                _r_valid = True
                for _cond in r.get('conditions', []):
                    _col = _cond.get('feature')
                    if _col not in X.columns:
                        _r_valid = False
                        break
                    _vals = X[_col]
                    _op   = _cond.get('operator')
                    _val  = _cond.get('value')
                    if _op == '<=':
                        _r_mask &= (_vals <= _val)
                    elif _op == '>':
                        _r_mask &= (_vals > _val)
                    elif _op == '<':
                        _r_mask &= (_vals < _val)
                    elif _op == '>=':
                        _r_mask &= (_vals >= _val)
                    else:
                        _r_valid = False
                        break
                if not _r_valid:
                    r['action']   = 'BOTH'  # conservative default
                    r['avg_pips'] = 0.0
                    continue

                # Get the candle timestamps where this rule fires.
                # The matrix dataframe used inside _build_candle_matrix_for_tf
                # was discarded — we only kept X here. To recover timestamps
                # we look up the matching X.index values in tf_ind which
                # IS still in scope from the build step. tf_ind has a
                # 'timestamp' column.
                _matching_x_idx = X.index[_r_mask].tolist()
                if not _matching_x_idx:
                    r['action']   = 'BOTH'
                    r['avg_pips'] = 0.0
                    continue

                # Map X row indices back to timestamps. The X frame was
                # built from a sampled subset of full_ind inside
                # _build_candle_matrix_for_tf; its index values are the
                # original full_ind row positions. To get timestamps we
                # need the timestamp column from full_ind — which is
                # gone by this point. Workaround: rebuild a tiny
                # timestamp lookup from tf_ind sorted positionally.
                # We accept a small approximation: use tf_ind ordered
                # by timestamp and index by integer position.
                # NOTE: this is an O(n_pos) lookup, not O(n_candles),
                # so it stays fast.
                from project2_backtesting.strategy_backtester import (
                    _load_tf_indicators as _a31_loader,
                )
                _a31_tf_ind = _a31_loader(tf, data_dir, needed_indicators=None)
                if _a31_tf_ind is None or 'timestamp' not in _a31_tf_ind.columns:
                    r['action']   = 'BOTH'
                    r['avg_pips'] = 0.0
                    continue

                # Index X.index values into tf_ind positionally
                _a31_ts_series = pd.to_datetime(_a31_tf_ind['timestamp'])
                _safe_idx = [i for i in _matching_x_idx if 0 <= i < len(_a31_ts_series)]
                if not _safe_idx:
                    r['action']   = 'BOTH'
                    r['avg_pips'] = 0.0
                    continue
                _matching_candle_ts = set(_a31_ts_series.iloc[_safe_idx].tolist())

                # Find trades that fall into those candles
                _trade_in_rule = _a31_trade_lookup['bucket'].isin(_matching_candle_ts)
                _matched_trades = _a31_trade_lookup[_trade_in_rule]
                _n_matched = len(_matched_trades)

                if _n_matched == 0:
                    # Rule fires on candles where the bot never actually
                    # opened a trade — purely synthetic, no direction
                    # info available. Mark BOTH so A.30 expands it and
                    # the user can see which direction works.
                    r['action']   = 'BOTH'
                    r['avg_pips'] = 0.0
                    continue

                _n_buy  = int(_matched_trades['is_buy'].sum())
                _n_sell = int(_matched_trades['is_sell'].sum())
                _n_dir  = _n_buy + _n_sell
                if _n_dir == 0:
                    r['action'] = 'BOTH'
                elif _n_buy / _n_dir >= _A31_DIR_THRESHOLD:
                    r['action'] = 'BUY'
                elif _n_sell / _n_dir >= _A31_DIR_THRESHOLD:
                    r['action'] = 'SELL'
                else:
                    r['action'] = 'BOTH'

                # Real expectancy from matched trades' pips
                try:
                    _avg_pips_val = float(_matched_trades['pips'].mean())
                except Exception:
                    _avg_pips_val = 0.0
                r['avg_pips'] = round(_avg_pips_val, 1)
                r['n_real_trades_in_rule'] = int(_n_matched)

            except Exception as _e:
                # If anything in the lookup fails, default to BOTH and
                # log it. A.30 will still backtest both directions.
                _log(f"  [{tf}] rule action tagging failed: {type(_e).__name__}: {_e}", cb)
                r['action']   = 'BOTH'
                r['avg_pips'] = 0.0

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
    # WHY (Phase A.34): Old call used wrong function name AND was
    #      missing the required `max_rules` positional argument.
    #      The real `_deduplicate_rules` signature is
    #      `_deduplicate_rules(rules, max_rules)` — rules first,
    #      max count second. We forward the outer function's
    #      `max_rules` parameter (which defaults to 25 and is
    #      controlled by the bot_entry_max_rules config key
    #      written by the Run Scenarios panel spinbox).
    # CHANGED: April 2026 — Phase A.34
    _log(f"Total rules across all TFs: {len(all_rules)}", cb)
    deduped = _deduplicate_rules(all_rules, max_rules)
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

    # WHY (Phase A.40a): Step 4 (bot-entry discovery) is the second
    #      independent rule-discovery path. Its rules previously lived
    #      only in bot_entry_rules.json; pipe them into the shared
    #      saved_rules.json library so they're visible alongside Step 3
    #      and Mode A rules.
    # CHANGED: April 2026 — Phase A.40a / A.40a.2 / A.40a.3

    # WHY (Phase A.40a.3): Loud entry line OUTSIDE try/except.
    # CHANGED: April 2026 — Phase A.40a.3
    _log(
        f"[A.40a.3] >>> ENTERING Step 4 auto-save hook "
        f"({len(deduped)} rules to process)",
        cb,
    )
    if deduped:
        _first = deduped[0]
        _log(
            f"[A.40a.3]   first rule keys={sorted(_first.keys()) if isinstance(_first, dict) else type(_first).__name__}, "
            f"n_conditions={len(_first.get('conditions', [])) if isinstance(_first, dict) else '?'}, "
            f"prediction={_first.get('prediction', 'MISSING') if isinstance(_first, dict) else '?'}",
            cb,
        )
    try:
        from shared.rule_library_bridge import (
            auto_save_discovered_rules as _a40a_save,
            is_auto_save_enabled as _a40a_enabled,
        )
        if not _a40a_enabled():
            _log(
                f"[A.40a.2] Step 4 auto-save DISABLED via global checkbox "
                f"— {len(deduped)} discovered rule(s) NOT piped into library",
                cb,
            )
        else:
            try:
                from shared.saved_rules import load_all as _a40a_load_all
                _a40a_size_before = len(_a40a_load_all() or [])
            except Exception:
                _a40a_size_before = -1
            _a40a_total_saved = 0
            _a40a_total_dedup = 0
            _a40a_total_invalid = 0
            _a40a_first_diag = None
            for _r in deduped:
                _tf = str(_r.get('timeframe', _r.get('tf', '?')))
                _act = str(_r.get('action', '?'))
                _src = f"Step4:{_tf}:{_act}"
                _s, _d, _i, _diag = _a40a_save([_r], source=_src, dedup=True)
                _a40a_total_saved   += _s
                _a40a_total_dedup   += _d
                _a40a_total_invalid += _i
                if _diag is not None and _a40a_first_diag is None:
                    _a40a_first_diag = _diag
            try:
                _a40a_size_after = len(_a40a_load_all() or [])
            except Exception:
                _a40a_size_after = -1
            _log(
                f"[A.40a] Step 4 auto-save: "
                f"saved={_a40a_total_saved}, dedup-skipped={_a40a_total_dedup}, "
                f"invalid={_a40a_total_invalid} "
                f"(library: {_a40a_size_before} → {_a40a_size_after})",
                cb,
            )
            if _a40a_total_invalid > 0 and _a40a_first_diag is not None:
                _log(
                    f"[A.40a.2] Step 4 first invalid rule reason: "
                    f"{_a40a_first_diag.get('reason')}; "
                    f"sample={_a40a_first_diag.get('sample')}",
                    cb,
                )
    except Exception as _a40a_e:
        _log(
            f"[A.40a] Step 4 auto-save skipped: "
            f"{type(_a40a_e).__name__}: {_a40a_e}",
            cb,
        )

    return result

"""
XGBoost Discovery — gradient boosting rule extraction for Project 1.

Trains an XGBoost classifier on the feature matrix (optionally with smart
features), then extracts human-readable IF/THEN rules from shallow decision
trees fitted on the XGBoost leaf embeddings.

Outputs:
  outputs/xgboost_result.json   — metrics + extracted rules
  outputs/xgboost_model.pkl     — saved XGBoost model

Pipeline integration:
  activate_xgboost_rules()  — patches analysis_report.json with XGBoost rules
  restore_original_rules()  — reverts analysis_report.json from backup
"""

import os
import json
import shutil
import joblib

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
from sklearn.tree import DecisionTreeClassifier

_HERE      = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_HERE, 'outputs')

RESULT_PATH  = os.path.join(OUTPUT_DIR, 'xgboost_result.json')
MODEL_PATH   = os.path.join(OUTPUT_DIR, 'xgboost_model.pkl')
ANALYSIS_PATH = os.path.join(OUTPUT_DIR, 'analysis_report.json')
BACKUP_PATH   = os.path.join(OUTPUT_DIR, 'analysis_report_backup.json')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg, cb):
    if cb:
        cb(msg)


def _load_feature_matrix(use_smart_features):
    """Load the appropriate feature matrix CSV."""
    from smart_features import CACHE_PATH as smart_path, compute_smart_features

    smart_exists = os.path.exists(smart_path)

    if use_smart_features:
        if smart_exists:
            df = pd.read_csv(smart_path)
        else:
            # Compute on demand — find base matrix
            base_candidates = []
            for scenario in ['H1_M15', 'M5', 'M15', 'H1', 'H4']:
                p = os.path.join(OUTPUT_DIR, f'scenario_{scenario}', 'feature_matrix_labeled.csv')
                if os.path.exists(p):
                    base_candidates.append(p)
            if not base_candidates:
                raise FileNotFoundError(
                    "No feature_matrix_labeled.csv found. Run Step 3 first."
                )
            df = compute_smart_features(base_candidates[0])
    else:
        # Plain feature matrix — pick best available scenario
        for scenario in ['H1_M15', 'M5', 'M15', 'H1', 'H4']:
            p = os.path.join(OUTPUT_DIR, f'scenario_{scenario}', 'feature_matrix_labeled.csv')
            if os.path.exists(p):
                return pd.read_csv(p), scenario
        raise FileNotFoundError("No feature_matrix_labeled.csv found. Run Step 3 first.")

    # Detect scenario tag from columns or default
    return df, 'smart'


def _prep_Xy(df, train_split):
    """Split df into train/test X, y arrays."""
    EXCLUDE = {'trade_id', 'open_time', 'action', 'profit', 'pips',
               'outcome', 'direction', 'dataset'}
    feature_cols = [c for c in df.columns if c not in EXCLUDE]

    if 'dataset' in df.columns:
        train_df = df[df['dataset'] == 'train'].copy()
        test_df  = df[df['dataset'] == 'test'].copy()
    else:
        split_idx = int(len(df) * train_split)
        train_df  = df.iloc[:split_idx].copy()
        test_df   = df.iloc[split_idx:].copy()

    X_train = train_df[feature_cols].fillna(0)
    y_train = train_df['outcome']
    X_test  = test_df[feature_cols].fillna(0)
    y_test  = test_df['outcome']

    return X_train, y_train, X_test, y_test, feature_cols


# ── Rule extraction ───────────────────────────────────────────────────────────

def _extract_rules_from_tree(tree, feature_cols, X_train, y_train,
                              X_test, y_test, min_coverage, min_win_rate):
    """Walk a single decision tree and collect WIN leaf paths.

    WHY: Old version verified rules on TRAIN data → reported in-sample win
         rate as if it were generalizable. Now verifies on TEST data so the
         user sees realistic numbers, with TRAIN kept as an overfit signal.
    CHANGED: April 2026 — held-out validation
    """
    tree_  = tree.tree_
    rules  = []

    def recurse(node_id, conditions):
        if tree_.feature[node_id] == -2:          # leaf
            samples    = tree_.n_node_samples[node_id]
            values     = tree_.value[node_id][0]
            total      = values.sum()
            win_count  = values[1] if len(values) > 1 else 0
            train_wr_internal = win_count / total if total > 0 else 0

            # Pre-filter on training-side metrics (cheap)
            if not (win_count >= (total - win_count)
                    and samples >= min_coverage
                    and train_wr_internal >= min_win_rate):
                return

            # ── Verify on TRAIN data (for overfit gap reference) ──────────
            train_mask = pd.Series(True, index=X_train.index)
            for cond in conditions:
                col_vals = X_train[cond['feature']]
                if cond['operator'] == '<=':
                    train_mask &= col_vals <= cond['value']
                else:
                    train_mask &= col_vals > cond['value']

            train_cov = int(train_mask.sum())
            if train_cov < min_coverage:
                return
            train_wr = float(y_train[train_mask].mean()) if train_cov > 0 else 0.0

            # ── Verify on TEST data (the real validation) ─────────────────
            # WHY: This is what determines if the rule generalizes. Train
            #      win rate is just how well the tree memorized.
            # CHANGED: April 2026 — held-out test
            test_mask = pd.Series(True, index=X_test.index)
            for cond in conditions:
                col_vals = X_test[cond['feature']]
                if cond['operator'] == '<=':
                    test_mask &= col_vals <= cond['value']
                else:
                    test_mask &= col_vals > cond['value']

            test_cov = int(test_mask.sum())
            test_wr  = float(y_test[test_mask].mean()) if test_cov > 0 else 0.0

            # Filter: rule must perform on TEST set, not just train
            if test_cov < max(5, min_coverage // 4):
                return  # not enough test samples to be confident
            if test_wr < min_win_rate:
                return  # looks good on train but failed on test → overfit

            overfit_gap = train_wr - test_wr

            rules.append({
                'conditions':     conditions.copy(),
                'prediction':     'WIN',
                # Headline numbers from TEST set
                'confidence':     round(test_wr, 3),
                'win_rate':       round(test_wr, 3),
                'coverage':       test_cov,
                'coverage_pct':   round(test_cov / len(X_test) * 100, 1),
                # Train metrics for overfit detection
                'train_win_rate': round(train_wr, 3),
                'train_coverage': train_cov,
                'overfit_gap':    round(overfit_gap, 3),
                'is_overfit':     overfit_gap > 0.15,
                'avg_pips':       0.0,
                'source':         'xgboost',
            })
            return

        feature   = feature_cols[tree_.feature[node_id]]
        threshold = round(float(tree_.threshold[node_id]), 4)

        recurse(tree_.children_left[node_id],
                conditions + [{'feature': feature, 'operator': '<=', 'value': threshold}])
        recurse(tree_.children_right[node_id],
                conditions + [{'feature': feature, 'operator': '>',  'value': threshold}])

    recurse(0, [])
    return rules


def _extract_xgboost_leaf_rules(booster, X_train, y_train, X_test, y_test,
                                  feature_cols, max_rules, max_depth,
                                  min_coverage, min_win_rate):
    """
    Fit a shallow decision tree on the original feature space and extract rules.
    Returns de-duplicated rule list.

    WHY: The previous version built leaf-index embeddings (OneHotEncoder on
         booster.apply() output) and computed combo_train, but then fitted the
         DecisionTree on X_train/y_train anyway — the embedding was never used.
         Dead code removed; the DT now fits directly on the original features
         so rules reference real indicator names, not opaque leaf IDs.
    CHANGED: April 2026 — removed dead leaf-encoding block
    """
    dt = DecisionTreeClassifier(
        max_depth=max_depth,
        min_samples_leaf=max(min_coverage, 10),
        min_samples_split=max(min_coverage * 2, 20),
        random_state=42,
    )
    dt.fit(X_train, y_train)

    raw_rules = _extract_rules_from_tree(dt, feature_cols,
                                          X_train, y_train,
                                          X_test, y_test,
                                          min_coverage, min_win_rate)
    return raw_rules


def _deduplicate_rules(rules, max_rules):
    """Remove rules that are strict supersets of shorter rules."""
    rules = sorted(rules, key=lambda r: r['win_rate'] * r['coverage'], reverse=True)
    kept  = []

    for candidate in rules:
        if len(kept) >= max_rules:
            break
        c_feats = {cond['feature'] for cond in candidate['conditions']}
        dominated = False
        for existing in kept:
            e_feats = {cond['feature'] for cond in existing['conditions']}
            if e_feats.issubset(c_feats) and existing['win_rate'] >= candidate['win_rate']:
                dominated = True
                break
        if not dominated:
            kept.append(candidate)

    return kept


# ── Main discovery function ───────────────────────────────────────────────────

def run_xgboost_discovery(
    max_rules=25,
    max_depth=4,
    n_estimators=300,
    min_coverage=10,
    min_win_rate=0.55,
    use_smart_features=True,
    train_test_split=0.7,
    progress_callback=None,
):
    """
    Full XGBoost discovery pipeline.

    Returns dict with keys:
      xgb_metrics, dt_metrics, rules, feature_importance, status
    """
    try:
        from xgboost import XGBClassifier
    except ImportError:
        raise ImportError("xgboost not installed. Run: pip install xgboost")

    cb = progress_callback
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ── 1. Load data ──────────────────────────────────────────────────────────
    _log("Loading feature matrix...", cb)
    df, scenario = _load_feature_matrix(use_smart_features)
    _log(f"  Loaded {len(df)} trades (scenario: {scenario})", cb)

    if 'outcome' not in df.columns:
        raise ValueError("Feature matrix missing 'outcome' column. Run Step 3 first.")

    X_train, y_train, X_test, y_test, feature_cols = _prep_Xy(df, train_test_split)
    _log(f"  Train: {len(X_train)}, Test: {len(X_test)}, Features: {len(feature_cols)}", cb)

    # ── 2. Train XGBoost ──────────────────────────────────────────────────────
    _log("Training XGBoost classifier...", cb)
    xgb = XGBClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=min_coverage,
        random_state=42,
        n_jobs=-1,
        verbosity=0,
        eval_metric='logloss',
    )
    xgb.fit(X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False)

    _log("XGBoost training complete.", cb)

    # ── 3. XGBoost metrics ────────────────────────────────────────────────────
    y_pred_xgb  = xgb.predict(X_test)
    y_proba_xgb = xgb.predict_proba(X_test)[:, 1]

    xgb_metrics = {
        'accuracy':  round(float(accuracy_score(y_test, y_pred_xgb)), 4),
        'precision': round(float(precision_score(y_test, y_pred_xgb, zero_division=0)), 4),
        'recall':    round(float(recall_score(y_test, y_pred_xgb, zero_division=0)), 4),
        'f1':        round(float(f1_score(y_test, y_pred_xgb, zero_division=0)), 4),
    }
    try:
        xgb_metrics['roc_auc'] = round(float(roc_auc_score(y_test, y_proba_xgb)), 4)
    except ValueError:
        xgb_metrics['roc_auc'] = 0.5

    _log(f"  XGBoost accuracy: {xgb_metrics['accuracy']:.3f}, ROC-AUC: {xgb_metrics['roc_auc']:.3f}", cb)

    # ── 4. Baseline DT metrics (for comparison) ───────────────────────────────
    _log("Training baseline Decision Tree...", cb)
    dt_base = DecisionTreeClassifier(max_depth=5, min_samples_leaf=20, random_state=42)
    dt_base.fit(X_train, y_train)
    y_pred_dt = dt_base.predict(X_test)

    dt_metrics = {
        'accuracy':  round(float(accuracy_score(y_test, y_pred_dt)), 4),
        'precision': round(float(precision_score(y_test, y_pred_dt, zero_division=0)), 4),
        'recall':    round(float(recall_score(y_test, y_pred_dt, zero_division=0)), 4),
        'f1':        round(float(f1_score(y_test, y_pred_dt, zero_division=0)), 4),
    }

    # ── 5. Feature importance ─────────────────────────────────────────────────
    _log("Computing feature importance...", cb)
    importances = xgb.feature_importances_
    feat_imp = sorted(
        zip(feature_cols, importances.tolist()),
        key=lambda x: x[1], reverse=True
    )
    top_20 = [(f, round(float(i), 6)) for f, i in feat_imp[:20]]

    # ── 6. Extract rules ──────────────────────────────────────────────────────
    _log("Extracting rules from XGBoost trees...", cb)
    raw_rules = _extract_xgboost_leaf_rules(
        xgb, X_train, y_train, X_test, y_test,
        feature_cols, max_rules, max_depth, min_coverage, min_win_rate,
    )
    rules = _deduplicate_rules(raw_rules, max_rules)
    _log(f"  Extracted {len(rules)} rules (from {len(raw_rules)} raw)", cb)

    # ── 7. Save model ─────────────────────────────────────────────────────────
    joblib.dump(xgb, MODEL_PATH)
    _log(f"  Saved model: {MODEL_PATH}", cb)

    # ── 8. Save result JSON ───────────────────────────────────────────────────
    result = {
        'status':       'ok',
        'scenario':     scenario,
        'n_trades':     len(df),
        'n_train':      len(X_train),
        'n_test':       len(X_test),
        'n_features':   len(feature_cols),
        'use_smart':    use_smart_features,
        'params': {
            'max_rules':        max_rules,
            'max_depth':        max_depth,
            'n_estimators':     n_estimators,
            'min_coverage':     min_coverage,
            'min_win_rate':     min_win_rate,
            'train_test_split': train_test_split,
        },
        'xgb_metrics':        xgb_metrics,
        'dt_metrics':         dt_metrics,
        'rules':              rules,
        'feature_importance': top_20,
    }

    with open(RESULT_PATH, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    _log(f"  Saved result: {RESULT_PATH}", cb)

    return result


# ── Pipeline integration ──────────────────────────────────────────────────────

def load_xgboost_result():
    """Return the last saved xgboost_result.json, or None."""
    if not os.path.exists(RESULT_PATH):
        return None
    with open(RESULT_PATH) as f:
        return json.load(f)


def activate_xgboost_rules():
    """
    Patch analysis_report.json with XGBoost rules (backup original first).
    Returns (success: bool, message: str).
    """
    if not os.path.exists(RESULT_PATH):
        return False, "No XGBoost result found. Run discovery first."
    if not os.path.exists(ANALYSIS_PATH):
        return False, "analysis_report.json not found. Run analysis first."

    with open(RESULT_PATH) as f:
        xgb_result = json.load(f)

    rules = xgb_result.get('rules', [])
    if not rules:
        return False, "XGBoost result contains no rules."

    # WHY: If backup already exists, overwriting it with the currently-patched
    #      analysis_report would corrupt the clean original. Skip the copy so the
    #      user can always restore to the pre-XGBoost state.
    # CHANGED: April 2026 — skip backup if one already exists
    if not os.path.exists(BACKUP_PATH):
        shutil.copy2(ANALYSIS_PATH, BACKUP_PATH)

    with open(ANALYSIS_PATH) as f:
        report = json.load(f)

    report['rules']             = rules
    report['_xgboost_active']   = True
    report['_xgboost_metrics']  = xgb_result.get('xgb_metrics', {})
    # Sync metadata fields so P2 picks up the correct scenario / feature info
    report['_xgboost_scenario'] = xgb_result.get('scenario', '')
    report['_xgboost_n_train']  = xgb_result.get('n_train', 0)
    report['_xgboost_n_test']   = xgb_result.get('n_test', 0)
    report['_xgboost_n_features'] = xgb_result.get('n_features', 0)

    with open(ANALYSIS_PATH, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    return True, f"Activated {len(rules)} XGBoost rules in pipeline."


def restore_original_rules():
    """
    Restore analysis_report.json from backup.
    Returns (success: bool, message: str).
    """
    if not os.path.exists(BACKUP_PATH):
        return False, "No backup found. Original rules may already be active."

    shutil.copy2(BACKUP_PATH, ANALYSIS_PATH)
    os.remove(BACKUP_PATH)
    return True, "Original rules restored."

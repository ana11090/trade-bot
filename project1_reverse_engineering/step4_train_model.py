"""
STEP 4 — TRAIN ML MODEL
Trains a Random Forest classifier to predict trade outcomes.
Reports comprehensive evaluation metrics.
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, classification_report
import joblib

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)


# WHY: fillna(0) on indicator features is semantically wrong because 0 is a
#      meaningful value for RSI (max oversold), ADX (no trend), MACD (zero
#      cross), Stochastic, CCI, and many others. Warmup-period NaN must be
#      distinguishable from real 0s. -999 is a sentinel that no real
#      indicator can produce, and tree-based models (RandomForest, XGBoost)
#      naturally isolate sentinel rows in their own split branch.
# CHANGED: April 2026 — replace fillna(0) with sentinel (audit bug #12)
NAN_SENTINEL = -999.0

def fill_feature_nans(X):
    """Replace NaN/inf in a feature DataFrame with NAN_SENTINEL.

    Used by step4/5/6/7 + xgboost_discovery to handle indicator warmup
    periods without corrupting semantics. Trees will split on
    `feature < -500` to isolate sentinel rows from real values.
    """
    import numpy as np
    return X.replace([np.inf, -np.inf], np.nan).fillna(NAN_SENTINEL)


def prepare_features(data, scenario=None):
    """Transform raw labeled data into clean numeric features for ML.

    WHY: Both step4 (training) and step5 (SHAP analysis) need the same
         transform. Putting it in a shared function prevents drift.

    Drops target leakage:
        - is_winner: target itself
        - trade_duration_minutes: losses cut short, wins run long → leaks outcome
        - profit, pips, outcome, direction: target-related

    Filters by scenario so each scenario trains on its own TF features only:
        scenario='M5'      → only M5_* features (+ time/cyclic)
        scenario='M15'     → only M15_* features (+ time/cyclic)
        scenario='H1'      → only H1_* features (+ time/cyclic)
        scenario='H4'      → only H4_* features (+ time/cyclic)
        scenario='D1'      → only D1_* features (+ time/cyclic)
        scenario='H1_M15'  → H1_* + M15_* features (+ time/cyclic)
        scenario=None      → all features (legacy behavior)

    WHY: Without this, all 5 scenarios train on all 638 features and produce
         identical models. With it, each scenario answers a different question:
         "can I predict outcome from ONLY [TF] data?"
    CHANGED: April 2026 — scenario-aware feature filtering
    """
    # Columns we never want as features
    leak_cols = {
        # Target columns and direct leakage
        'trade_id', 'profit', 'pips', 'outcome', 'direction', 'dataset',
        'is_winner',               # LEAKAGE: this IS the target
        'trade_duration_minutes',  # LEAKAGE: wins held longer than losses
        # Pure metadata
        'order_id', 'ticket', 'magic', 'comment', 'symbol',
    }

    # Extract time features from timestamp columns
    for ts_col in ['open_time', 'close_time']:
        if ts_col not in data.columns:
            continue
        try:
            ts = pd.to_datetime(data[ts_col], errors='coerce')
            prefix = ts_col.replace('_time', '')

            hour = ts.dt.hour.fillna(0)
            dow = ts.dt.dayofweek.fillna(0)
            month = ts.dt.month.fillna(1)

            data[f'{prefix}_hour'] = hour
            data[f'{prefix}_dow'] = dow
            data[f'{prefix}_month'] = month
            data[f'{prefix}_hour_sin'] = np.sin(2 * np.pi * hour / 24)
            data[f'{prefix}_hour_cos'] = np.cos(2 * np.pi * hour / 24)
            data[f'{prefix}_dow_sin'] = np.sin(2 * np.pi * dow / 7)
            data[f'{prefix}_dow_cos'] = np.cos(2 * np.pi * dow / 7)

            leak_cols.add(ts_col)
        except Exception as e:
            log.info(f"  Could not extract time features from {ts_col}: {e}")
            leak_cols.add(ts_col)

    # Label-encode any remaining string columns
    for col in list(data.columns):
        if col in leak_cols:
            continue
        if pd.api.types.is_numeric_dtype(data[col]):
            continue
        try:
            converted = pd.to_numeric(data[col], errors='coerce')
            if converted.notna().sum() > len(converted) * 0.8:
                # WHY (Phase 75 Fix 55): No log of coercion. 20% non-numeric
                #      values became 0 silently — garbage features in model.
                # CHANGED: April 2026 — Phase 75 Fix 55 — log coerced columns
                _n_bad = converted.isna().sum()
                if _n_bad > 0:
                    log.warning(f"  [step4] '{col}': coerced {_n_bad} "
                                f"non-numeric values to 0 "
                                f"({_n_bad/len(converted)*100:.0f}% of rows)")
                data[col] = converted.fillna(0)
                continue
        except Exception:
            pass
        try:
            unique_vals = data[col].astype(str).unique()
            if len(unique_vals) <= 50:
                # WHY (Phase 75 Fix 56): Lexicographic integer encoding makes
                #      RF learn spurious ordinal relationships ('Buy'=0 < 'Sell'=1
                #      implies Sell > Buy). Use one-hot for nominal categoricals.
                # CHANGED: April 2026 — Phase 75 Fix 56 — one-hot encoding
                _dummies = pd.get_dummies(data[col].astype(str), prefix=col, drop_first=False)
                data = pd.concat([data.drop(columns=[col]), _dummies], axis=1)
                log.info(f"  [step4] One-hot encoded '{col}': "
                         f"{list(_dummies.columns)}")
            else:
                leak_cols.add(col)
        except Exception:
            leak_cols.add(col)

    # All numeric non-leak columns are candidates
    candidate_cols = [
        col for col in data.columns
        if col not in leak_cols and pd.api.types.is_numeric_dtype(data[col])
    ]

    # ── Apply scenario filter ─────────────────────────────────────────────────
    # WHY: Each scenario trains on features from ITS timeframe(s) only.
    #      Without this, all scenarios see all 638 features and produce
    #      identical models — the "5 scenarios, 5 same answers" bug.
    # CHANGED: April 2026 — scenario-aware feature filtering
    if scenario is None or scenario == 'all':
        feature_cols = candidate_cols
        log.info(f"  [SCENARIO=all] Using all {len(feature_cols)} features")
    else:
        scenario_tfs = {
            'M5':     ['M5'],
            'M15':    ['M15'],
            'H1':     ['H1'],
            'H4':     ['H4'],
            'D1':     ['D1'],
            'H1_M15': ['H1', 'M15'],
            'H4_H1':  ['H4', 'H1'],
            'H1_M5':  ['H1', 'M5'],
        }
        # WHY (Phase 75 Fix 57): Fallback [scenario] uses the scenario string
        #      itself as a prefix. A typo ('h1' instead of 'H1') produces no
        #      matching columns and the model trains on zero features silently.
        # CHANGED: April 2026 — Phase 75 Fix 57 — warn on empty feature_cols
        allowed_prefixes = scenario_tfs.get(scenario, [scenario])
        if scenario not in scenario_tfs:
            log.warning(f"  [step4] Scenario '{scenario}' not in known TF map "
                        f"— using prefix '{scenario}'; may produce empty feature set")

        # Time/cyclic features have no TF prefix — always include them
        non_tf_keepers = {
            'open_hour', 'open_dow', 'open_month',
            'open_hour_sin', 'open_hour_cos', 'open_dow_sin', 'open_dow_cos',
            'close_hour', 'close_dow', 'close_month',
            'close_hour_sin', 'close_hour_cos', 'close_dow_sin', 'close_dow_cos',
            'hour_of_day', 'day_of_week', 'trade_direction',
        }

        feature_cols = []
        for col in candidate_cols:
            if col in non_tf_keepers:
                feature_cols.append(col)
                continue
            if any(col.startswith(f'{tf}_') for tf in allowed_prefixes):
                feature_cols.append(col)

        log.info(f"  [SCENARIO={scenario}] Filtered to {len(feature_cols)} features "
              f"from prefixes {allowed_prefixes} (out of {len(candidate_cols)} total)")

        # Phase 75 Fix 57: Guard empty feature_cols early
        if not feature_cols:
            log.error(f"  [step4] NO features selected for scenario '{scenario}'! "
                      f"Allowed prefixes: {allowed_prefixes}. "
                      f"Check scenario name and available TF columns.")
            return None, []

    return data, feature_cols


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

from config_loader import load as _load_cfg
_cfg                 = _load_cfg()
RF_N_ESTIMATORS      = int(_cfg['rf_trees'])
RF_MAX_DEPTH         = int(_cfg['max_tree_depth'])
RF_MIN_SAMPLES_LEAF  = int(_cfg['min_samples_leaf'])
RF_RANDOM_STATE      = 42


def train_model_for_scenario(scenario):
    """
    Train Random Forest model for a specific scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    log.info(f"\n{'=' * 60}")
    log.info(f"[STEP 4/7] Training ML model — scenario: {scenario}")
    log.info(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load labeled feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')

        if not os.path.exists(feature_file):
            log.error(f"Labeled feature matrix not found: {feature_file}")
            log.info(f"FIX: Run step3_label_trades.py first for scenario {scenario}")
            return False

        data = pd.read_csv(feature_file)
        data['open_time'] = pd.to_datetime(data['open_time'])

        log.info(f"  Loaded labeled data: {len(data)} trades")

        # Transform features and split AFTER (shared with step5 SHAP analysis)
        data, feature_cols = prepare_features(data, scenario=scenario)

        log.info(f"  Feature count: {len(feature_cols)} (numeric, no leakage)")

        if not feature_cols:
            log.info(f"  ERROR: No usable feature columns found!")
            return False

        # Split AFTER transform so new columns exist in both subsets
        train_data = data[data['dataset'] == 'train'].copy()
        test_data = data[data['dataset'] == 'test'].copy()
        log.info(f"  Train set: {len(train_data)} trades")
        log.info(f"  Test set: {len(test_data)} trades")

        # WHY: Sentinel fill preserves indicator semantics (RSI=0 is max
        #      oversold, not missing). See NAN_SENTINEL comment above.
        # CHANGED: April 2026 — replace fillna(0) with sentinel (audit bug #12)
        X_train = fill_feature_nans(train_data[feature_cols])
        y_train = train_data['outcome']

        X_test = fill_feature_nans(test_data[feature_cols])
        y_test = test_data['outcome']

        # Train Random Forest classifier
        log.info(f"\n  Training Random Forest classifier...")
        log.info(f"    n_estimators: {RF_N_ESTIMATORS}")
        log.info(f"    max_depth: {RF_MAX_DEPTH}")
        log.info(f"    min_samples_leaf: {RF_MIN_SAMPLES_LEAF}")
        log.info(f"    random_state: {RF_RANDOM_STATE}")

        model = RandomForestClassifier(
            n_estimators=RF_N_ESTIMATORS,
            max_depth=RF_MAX_DEPTH,
            min_samples_leaf=RF_MIN_SAMPLES_LEAF,
            random_state=RF_RANDOM_STATE,
            n_jobs=-1,  # Use all CPU cores
            verbose=0
        )

        model.fit(X_train, y_train)

        log.info(f"  Model trained successfully")

        # Make predictions on test set
        y_pred = model.predict(X_test)
        y_pred_proba = model.predict_proba(X_test)[:, 1]  # Probability of class 1 (win)

        # Calculate evaluation metrics
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred, zero_division=0)
        recall = recall_score(y_test, y_pred, zero_division=0)
        f1 = f1_score(y_test, y_pred, zero_division=0)

        # ROC-AUC (handle case where only one class is present)
        try:
            roc_auc = roc_auc_score(y_test, y_pred_proba)
        except ValueError:
            roc_auc = 0.5  # Default for single-class case

        # Training set metrics (for comparison)
        y_train_pred = model.predict(X_train)
        train_accuracy = accuracy_score(y_train, y_train_pred)

        # Print evaluation report
        log.info(f"\n  {'=' * 50}")
        log.info(f"  EVALUATION METRICS — {scenario}")
        log.info(f"  {'=' * 50}")
        log.info(f"  Test Set Performance:")
        log.info(f"    Accuracy:  {accuracy:.3f} ({accuracy*100:.1f}%)")
        log.info(f"    Precision: {precision:.3f}")
        log.info(f"    Recall:    {recall:.3f}")
        log.info(f"    F1 Score:  {f1:.3f}")
        log.info(f"    ROC-AUC:   {roc_auc:.3f}")
        log.info(f"")
        log.info(f"  Train Set Performance:")
        log.info(f"    Accuracy:  {train_accuracy:.3f} ({train_accuracy*100:.1f}%)")
        log.info(f"")
        log.info(f"  Baseline (always predict majority class): {y_test.value_counts(normalize=True).max():.3f}")
        log.info(f"  {'=' * 50}\n")

        # Detailed classification report
        log.info("  Detailed Classification Report:")
        log.info(classification_report(y_test, y_pred, target_names=['Loss', 'Win'], zero_division=0))

        # Feature importance (top 20)
        feature_importance = pd.DataFrame({
            'feature': feature_cols,
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)

        log.info("\n  Top 20 Most Important Features:")
        for idx, row in feature_importance.head(20).iterrows():
            log.info(f"    {row['feature']:40s} {row['importance']:.4f}")

        # Save model
        model_file = os.path.join(output_dir, 'trained_model.pkl')
        joblib.dump(model, model_file)
        log.info(f"\n  Saved trained model: {model_file}")

        # Save feature importance
        importance_file = os.path.join(output_dir, 'feature_importance.csv')
        feature_importance.to_csv(importance_file, index=False)
        log.info(f"  Saved feature importance: {importance_file}")

        # Save metrics to file
        metrics_file = os.path.join(output_dir, 'model_metrics.txt')
        with open(metrics_file, 'w', encoding='utf-8') as f:
            f.write(f"MODEL EVALUATION METRICS — {scenario}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(f"Test Set Performance:\n")
            f.write(f"  Accuracy:  {accuracy:.3f} ({accuracy*100:.1f}%)\n")
            f.write(f"  Precision: {precision:.3f}\n")
            f.write(f"  Recall:    {recall:.3f}\n")
            f.write(f"  F1 Score:  {f1:.3f}\n")
            f.write(f"  ROC-AUC:   {roc_auc:.3f}\n\n")
            f.write(f"Train Set Performance:\n")
            f.write(f"  Accuracy:  {train_accuracy:.3f} ({train_accuracy*100:.1f}%)\n\n")
            f.write(f"Baseline: {y_test.value_counts(normalize=True).max():.3f}\n\n")
            f.write(f"Classification Report:\n")
            f.write(classification_report(y_test, y_pred, target_names=['Loss', 'Win'], zero_division=0))

        log.info(f"  Saved metrics report: {metrics_file}")

        log.info(f"\n[STEP 4/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        log.info(f"\nERROR in step4 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Train Random Forest model for trade prediction')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = train_model_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()

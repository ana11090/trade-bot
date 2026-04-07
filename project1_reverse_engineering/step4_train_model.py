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

from shared import data_utils


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
            print(f"  Could not extract time features from {ts_col}: {e}")
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
                data[col] = converted.fillna(0)
                continue
        except Exception:
            pass
        try:
            unique_vals = data[col].astype(str).unique()
            if len(unique_vals) <= 50:
                mapping = {v: i for i, v in enumerate(sorted(unique_vals))}
                data[col] = data[col].astype(str).map(mapping).fillna(-1)
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
        print(f"  [SCENARIO=all] Using all {len(feature_cols)} features")
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
        allowed_prefixes = scenario_tfs.get(scenario, [scenario])

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

        print(f"  [SCENARIO={scenario}] Filtered to {len(feature_cols)} features "
              f"from prefixes {allowed_prefixes} (out of {len(candidate_cols)} total)")

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
    print(f"\n{'=' * 60}")
    print(f"[STEP 4/7] Training ML model — scenario: {scenario}")
    print(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load labeled feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')

        if not os.path.exists(feature_file):
            print(f"ERROR: Labeled feature matrix not found: {feature_file}")
            print(f"FIX: Run step3_label_trades.py first for scenario {scenario}")
            return False

        data = pd.read_csv(feature_file)
        data['open_time'] = pd.to_datetime(data['open_time'])

        print(f"  Loaded labeled data: {len(data)} trades")

        # Transform features and split AFTER (shared with step5 SHAP analysis)
        data, feature_cols = prepare_features(data, scenario=scenario)

        print(f"  Feature count: {len(feature_cols)} (numeric, no leakage)")

        if not feature_cols:
            print(f"  ERROR: No usable feature columns found!")
            return False

        # Split AFTER transform so new columns exist in both subsets
        train_data = data[data['dataset'] == 'train'].copy()
        test_data = data[data['dataset'] == 'test'].copy()
        print(f"  Train set: {len(train_data)} trades")
        print(f"  Test set: {len(test_data)} trades")

        # Prepare training data
        X_train = train_data[feature_cols].fillna(0)  # Fill any remaining NaN values
        y_train = train_data['outcome']

        # Prepare test data
        X_test = test_data[feature_cols].fillna(0)
        y_test = test_data['outcome']

        # Train Random Forest classifier
        print(f"\n  Training Random Forest classifier...")
        print(f"    n_estimators: {RF_N_ESTIMATORS}")
        print(f"    max_depth: {RF_MAX_DEPTH}")
        print(f"    min_samples_leaf: {RF_MIN_SAMPLES_LEAF}")
        print(f"    random_state: {RF_RANDOM_STATE}")

        model = RandomForestClassifier(
            n_estimators=RF_N_ESTIMATORS,
            max_depth=RF_MAX_DEPTH,
            min_samples_leaf=RF_MIN_SAMPLES_LEAF,
            random_state=RF_RANDOM_STATE,
            n_jobs=-1,  # Use all CPU cores
            verbose=0
        )

        model.fit(X_train, y_train)

        print(f"  Model trained successfully")

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
        print(f"\n  {'=' * 50}")
        print(f"  EVALUATION METRICS — {scenario}")
        print(f"  {'=' * 50}")
        print(f"  Test Set Performance:")
        print(f"    Accuracy:  {accuracy:.3f} ({accuracy*100:.1f}%)")
        print(f"    Precision: {precision:.3f}")
        print(f"    Recall:    {recall:.3f}")
        print(f"    F1 Score:  {f1:.3f}")
        print(f"    ROC-AUC:   {roc_auc:.3f}")
        print(f"")
        print(f"  Train Set Performance:")
        print(f"    Accuracy:  {train_accuracy:.3f} ({train_accuracy*100:.1f}%)")
        print(f"")
        print(f"  Baseline (always predict majority class): {y_test.value_counts(normalize=True).max():.3f}")
        print(f"  {'=' * 50}\n")

        # Detailed classification report
        print("  Detailed Classification Report:")
        print(classification_report(y_test, y_pred, target_names=['Loss', 'Win'], zero_division=0))

        # Feature importance (top 20)
        feature_importance = pd.DataFrame({
            'feature': feature_cols,
            'importance': model.feature_importances_
        }).sort_values('importance', ascending=False)

        print("\n  Top 20 Most Important Features:")
        for idx, row in feature_importance.head(20).iterrows():
            print(f"    {row['feature']:40s} {row['importance']:.4f}")

        # Save model
        model_file = os.path.join(output_dir, 'trained_model.pkl')
        joblib.dump(model, model_file)
        print(f"\n  Saved trained model: {model_file}")

        # Save feature importance
        importance_file = os.path.join(output_dir, 'feature_importance.csv')
        feature_importance.to_csv(importance_file, index=False)
        print(f"  Saved feature importance: {importance_file}")

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

        print(f"  Saved metrics report: {metrics_file}")

        print(f"\n[STEP 4/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        print(f"\nERROR in step4 — {scenario}: {str(e)}")
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

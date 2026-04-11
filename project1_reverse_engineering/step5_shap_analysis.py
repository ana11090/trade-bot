"""
STEP 5 — SHAP FEATURE IMPORTANCE ANALYSIS
Uses SHAP (SHapley Additive exPlanations) to reveal which indicators
the bot was most likely reacting to.
Generates visualizations showing feature importance and impact direction.
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import joblib
import shap

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

# WHY: share the NaN sentinel with step4 so train/test encode NaN identically
# CHANGED: April 2026 — replace fillna(0) (audit bug #12)
from step4_train_model import fill_feature_nans


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
SHAP_MAX_SAMPLES = 200  # Limit samples for faster SHAP computation


def shap_analysis_for_scenario(scenario):
    """
    Perform SHAP analysis for a specific scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    log.info(f"\n{'=' * 60}")
    log.info(f"[STEP 5/7] SHAP analysis — scenario: {scenario}")
    log.info(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load trained model
        model_file = os.path.join(output_dir, 'trained_model.pkl')

        if not os.path.exists(model_file):
            log.error(f"Trained model not found: {model_file}")
            log.info(f"FIX: Run step4_train_model.py first for scenario {scenario}")
            return False

        model = joblib.load(model_file)
        log.info(f"  Loaded trained model from: {model_file}")

        # Load labeled feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')
        data = pd.read_csv(feature_file)

        # WHY: Use the same transform as step4 so feature columns match exactly.
        #      Otherwise SHAP gets timestamps and crashes, OR uses different
        #      features than the trained model expects.
        # CHANGED: April 2026 — shared transform helper
        from step4_train_model import prepare_features
        data, feature_cols = prepare_features(data, scenario=scenario)

        log.info(f"  Feature count: {len(feature_cols)} (numeric)")

        # Get test data AFTER transform so new columns exist
        test_data = data[data['dataset'] == 'test'].copy()

        log.info(f"  Loaded test data: {len(test_data)} trades")

        X_test = fill_feature_nans(test_data[feature_cols])

        # Limit to SHAP_MAX_SAMPLES for faster computation
        if len(X_test) > SHAP_MAX_SAMPLES:
            log.info(f"  Limiting SHAP analysis to {SHAP_MAX_SAMPLES} samples (out of {len(X_test)}) for performance")
            X_test_shap = X_test.sample(n=SHAP_MAX_SAMPLES, random_state=42)
        else:
            X_test_shap = X_test

        # Create SHAP explainer
        log.info(f"\n  Computing SHAP values (this may take a few minutes)...")
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_test_shap)

        # For binary classification, SHAP can return:
        #   - List of arrays: [shap_for_class_0, shap_for_class_1]  (older SHAP)
        #   - 3D array of shape (samples, features, classes)        (newer SHAP)
        #   - 2D array of shape (samples, features)                 (regression / single output)
        # We want shap_values for class 1 (WIN).
        # WHY: Newer SHAP versions return 3D arrays which crash DataFrame construction.
        # CHANGED: April 2026 — handle 3D SHAP output
        if isinstance(shap_values, list):
            # Old SHAP: list of [class0_array, class1_array]
            shap_values_win = shap_values[1]
        elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 3:
            # New SHAP: 3D array (samples, features, classes) — slice class 1
            shap_values_win = shap_values[:, :, 1]
        else:
            # 2D array (samples, features) — already what we want
            shap_values_win = shap_values

        # Final sanity check: must be 2D
        if shap_values_win.ndim != 2:
            log.info(f"  WARNING: Unexpected SHAP shape {shap_values_win.shape} — flattening")
            shap_values_win = shap_values_win.reshape(len(X_test_shap), -1)

        log.info(f"  SHAP values computed successfully (shape: {shap_values_win.shape})")

        # Calculate mean absolute SHAP values for each feature
        mean_abs_shap = pd.DataFrame({
            'feature': feature_cols,
            'mean_abs_shap': np.abs(shap_values_win).mean(axis=0)
        }).sort_values('mean_abs_shap', ascending=False)

        log.info(f"\n  Top 20 Features by SHAP Importance:")
        for idx, row in mean_abs_shap.head(20).iterrows():
            log.info(f"    {row['feature']:40s} {row['mean_abs_shap']:.4f}")

        # Save top features
        top_features_file = os.path.join(output_dir, 'top_features_shap.txt')
        with open(top_features_file, 'w', encoding='utf-8') as f:
            f.write(f"TOP FEATURES BY SHAP IMPORTANCE — {scenario}\n")
            f.write(f"{'=' * 60}\n\n")
            for idx, row in mean_abs_shap.iterrows():
                f.write(f"{row['feature']:50s} {row['mean_abs_shap']:.6f}\n")

        log.info(f"\n  Saved top features: {top_features_file}")

        # Save SHAP importance CSV
        shap_importance_file = os.path.join(output_dir, 'shap_importance.csv')
        mean_abs_shap.to_csv(shap_importance_file, index=False)
        log.info(f"  Saved SHAP importance CSV: {shap_importance_file}")

        # VISUALIZATION 1 — Feature Importance Bar Chart
        log.info(f"\n  Generating SHAP bar chart...")
        plt.figure(figsize=(12, 8))
        top_n = min(20, len(mean_abs_shap))
        top_features_data = mean_abs_shap.head(top_n)

        plt.barh(range(top_n), top_features_data['mean_abs_shap'].values)
        plt.yticks(range(top_n), top_features_data['feature'].values)
        plt.xlabel('Mean Absolute SHAP Value')
        plt.title(f'Top {top_n} Features by SHAP Importance — {scenario}')
        plt.gca().invert_yaxis()
        plt.tight_layout()

        bar_chart_file = os.path.join(output_dir, 'shap_bar_chart.png')
        plt.savefig(bar_chart_file, dpi=150, bbox_inches='tight')
        plt.close()

        log.info(f"  Saved SHAP bar chart: {bar_chart_file}")

        # VISUALIZATION 2 — SHAP Summary Plot (Beeswarm)
        log.info(f"  Generating SHAP summary plot...")
        plt.figure(figsize=(12, 10))

        shap.summary_plot(
            shap_values_win,
            X_test_shap,
            feature_names=feature_cols,
            max_display=20,
            show=False
        )

        plt.title(f'SHAP Summary Plot — {scenario}', fontsize=14, pad=20)
        plt.tight_layout()

        summary_plot_file = os.path.join(output_dir, 'shap_summary.png')
        plt.savefig(summary_plot_file, dpi=150, bbox_inches='tight')
        plt.close()

        log.info(f"  Saved SHAP summary plot: {summary_plot_file}")

        log.info(f"\n[STEP 5/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        log.info(f"\nERROR in step5 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Perform SHAP analysis on trained model')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = shap_analysis_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()

"""
STEP 3 — LABEL TRADES
Adds win/loss labels and direction labels to the feature matrix.
Performs chronological train/test split.
"""

import sys
import os
import argparse
import pandas as pd

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from shared import data_utils


# ============================================================
# CONFIGURATION
# ============================================================
OUTPUT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

from config_loader import load as _load_cfg
_cfg                   = _load_cfg()
TRAIN_TEST_SPLIT_RATIO = float(_cfg['train_test_split'])


def label_trades_for_scenario(scenario):
    """
    Label trades with win/loss and direction for a specific scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"[STEP 3/7] Labeling trades — scenario: {scenario}")
    print(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    try:
        # Load feature matrix
        feature_file = os.path.join(output_dir, 'feature_matrix.csv')

        if not os.path.exists(feature_file):
            print(f"ERROR: Feature matrix file not found: {feature_file}")
            print(f"FIX: Run step2_compute_indicators.py first for scenario {scenario}")
            return False

        feature_matrix = pd.read_csv(feature_file)
        feature_matrix['open_time'] = pd.to_datetime(feature_matrix['open_time'])

        print(f"  Loaded feature matrix: {len(feature_matrix)} trades × {len(feature_matrix.columns)} columns")

        # PRIMARY LABEL — Win/Loss (outcome)
        # A trade is a win (1) if profit > 0, loss (0) if profit <= 0
        feature_matrix['outcome'] = (feature_matrix['profit'] > 0).astype(int)

        win_count = feature_matrix['outcome'].sum()
        loss_count = len(feature_matrix) - win_count
        win_rate = win_count / len(feature_matrix) * 100

        print(f"  Labeled outcomes: {win_count} wins ({win_rate:.1f}%), {loss_count} losses")

        # SECONDARY LABEL — Direction
        # 1 for Buy, 0 for Sell
        feature_matrix['direction'] = feature_matrix['action'].str.lower().map({'buy': 1, 'sell': 0})

        buy_count = feature_matrix['direction'].sum()
        sell_count = len(feature_matrix) - buy_count

        print(f"  Labeled directions: {buy_count} buys, {sell_count} sells")

        # TRAIN/TEST SPLIT — Chronological
        # Sort by open_time first
        feature_matrix = feature_matrix.sort_values('open_time').reset_index(drop=True)

        # Split at the specified ratio
        split_index = int(len(feature_matrix) * TRAIN_TEST_SPLIT_RATIO)

        feature_matrix['dataset'] = 'train'
        feature_matrix.loc[split_index:, 'dataset'] = 'test'

        train_count = (feature_matrix['dataset'] == 'train').sum()
        test_count = (feature_matrix['dataset'] == 'test').sum()

        print(f"  Train/test split: {train_count} train ({TRAIN_TEST_SPLIT_RATIO*100:.0f}%), {test_count} test")

        # Save labeled feature matrix
        output_file = os.path.join(output_dir, 'feature_matrix_labeled.csv')
        data_utils.save_dataframe(feature_matrix, output_file, "labeled feature matrix")

        # Print summary statistics
        print(f"\n  Summary:")
        print(f"    Total trades: {len(feature_matrix)}")
        print(f"    Win rate: {win_rate:.1f}%")
        print(f"    Train set: {train_count} trades")
        print(f"    Test set: {test_count} trades")
        print(f"    Feature count: {len([col for col in feature_matrix.columns if col not in ['trade_id', 'open_time', 'action', 'profit', 'pips', 'outcome', 'direction', 'dataset']])}")

        print(f"\n[STEP 3/7] COMPLETE — scenario: {scenario}\n")

        return True

    except Exception as e:
        print(f"\nERROR in step3 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Label trades with win/loss and direction')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = label_trades_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()

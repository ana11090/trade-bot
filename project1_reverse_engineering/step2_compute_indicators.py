"""
STEP 2 — COMPUTE INDICATORS
Computes all 119 technical indicators for each trade at the aligned candle timestamp.
Produces a feature matrix where each row is a trade and each column is an indicator value.
"""

import sys
import os
import argparse
import pandas as pd

# Add parent directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from shared import data_utils, indicator_utils


# ============================================================
# CONFIGURATION
# ============================================================
PRICE_DATA_FOLDER = '../data/'
OUTPUT_FOLDER = './outputs/'
SYMBOL = 'XAUUSD'


def compute_indicators_for_scenario(scenario):
    """
    Compute all indicators for a specific timeframe scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"[STEP 2/7] Computing indicators — scenario: {scenario}")
    print(f"{'=' * 60}\n")

    output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')

    # Determine which timeframes to process
    if scenario == 'H1_M15':
        timeframes = ['H1', 'M15']
    else:
        timeframes = [scenario]

    try:
        feature_matrix = None

        for tf in timeframes:
            print(f"\n--- Processing timeframe: {tf} ---")

            # Load aligned trades for this timeframe
            aligned_trades_file = os.path.join(output_dir, f'trades_with_candles_{tf}.csv')

            if not os.path.exists(aligned_trades_file):
                print(f"ERROR: Aligned trades file not found: {aligned_trades_file}")
                print(f"FIX: Run step1_align_price.py first for scenario {scenario}")
                return False

            trades_df = pd.read_csv(aligned_trades_file)
            trades_df['open_time'] = pd.to_datetime(trades_df['open_time'])
            trades_df['close_time'] = pd.to_datetime(trades_df['close_time'])
            trades_df['aligned_candle_timestamp'] = pd.to_datetime(trades_df['aligned_candle_timestamp'])

            # Load full OHLCV data for this timeframe
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')

            if not os.path.exists(candle_file):
                print(f"ERROR: Candle data file not found: {candle_file}")
                return False

            candles_df = data_utils.load_ohlcv_csv(candle_file, tf)

            # Ensure timestamp is datetime
            if candles_df['timestamp'].dt.tz is None:
                candles_df['timestamp'] = candles_df['timestamp'].dt.tz_localize('UTC')

            # Compute all indicators on the full candle dataset
            prefix = f'{tf}_' if scenario == 'H1_M15' else ''
            indicators_df = indicator_utils.compute_all_indicators(candles_df, prefix=prefix)

            # Build feature matrix - extract indicator values at each trade's aligned timestamp
            if feature_matrix is None:
                # First timeframe - create feature matrix with trade metadata
                feature_matrix = indicator_utils.build_feature_matrix(trades_df, indicators_df)
            else:
                # Second timeframe (H1_M15 scenario) - merge additional indicator columns
                additional_features = indicator_utils.build_feature_matrix(trades_df, indicators_df)

                # Get only the indicator columns (exclude metadata columns)
                metadata_cols = ['trade_id', 'open_time', 'action', 'profit', 'pips']
                indicator_cols = [col for col in additional_features.columns if col not in metadata_cols]

                # Add these columns to the existing feature matrix
                for col in indicator_cols:
                    feature_matrix[col] = additional_features[col].values

                print(f"  Merged {len(indicator_cols)} additional features from {tf}")

        # Save feature matrix
        output_file = os.path.join(output_dir, 'feature_matrix.csv')
        data_utils.save_dataframe(feature_matrix, output_file, "feature matrix")

        print(f"\n[STEP 2/7] COMPLETE — scenario: {scenario}")
        print(f"Feature matrix: {len(feature_matrix)} trades × {len(feature_matrix.columns)} features\n")

        return True

    except Exception as e:
        print(f"\nERROR in step2 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Compute technical indicators for trades')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = compute_indicators_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()

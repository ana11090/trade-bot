"""
STEP 2 — COMPUTE INDICATORS (Multi-Timeframe)
Computes all technical indicators for each trade across all aligned timeframes.
Produces a feature matrix with ~400 features (80 indicators × 5 timeframes).
"""

import sys
import os
import pandas as pd
import time

# Add parent directory to path
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, PROJECT_ROOT)

from shared import data_utils, indicator_utils
from config_loader import load as _load_cfg

# ── Paths ─────────────────────────────────────────────────────────────────────
PRICE_DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')
OUTPUT_FOLDER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

# ── Configuration ─────────────────────────────────────────────────────────────
_cfg                 = _load_cfg()
SYMBOL               = _cfg['symbol']
ALIGN_TIMEFRAMES     = _cfg['align_timeframes'].split(',')
LOOKBACK_CANDLES     = int(_cfg['lookback_candles'])
SKIP_M1              = _cfg.get('skip_m1_features', 'true').lower() == 'true'


def compute_features(aligned_trades_path=None, output_dir=None):
    """
    Compute all technical indicators for each trade across all aligned timeframes.

    For each trade and each timeframe:
    1. Look up the aligned candle index
    2. Get the lookback window (200 candles ending at that index)
    3. Compute all indicators on that window
    4. Take the LAST value (the value at the trade's entry moment)
    5. Add as columns with prefix: {tf}_ (e.g., H1_rsi_14, M5_macd_std, D1_atr_14)

    Output: feature_matrix.csv with one row per trade and columns like:
      trade_id, open_time, action, pips, profit, ...,
      M5_rsi_14, M5_rsi_21, ..., M5_macd_std, ...,
      H1_rsi_14, H1_rsi_21, ..., H1_macd_std, ...,
      D1_rsi_14, D1_rsi_21, ..., D1_macd_std, ...

    Args:
        aligned_trades_path: Optional path to aligned trades CSV
        output_dir: Optional output directory

    Returns:
        DataFrame with feature matrix, or None if failed
    """
    print(f"\n{'=' * 70}")
    print(f"[STEP 2/2] COMPUTING INDICATORS (Multi-Timeframe)")
    print(f"{'=' * 70}\n")

    try:
        # Get paths
        if output_dir is None:
            output_dir = OUTPUT_FOLDER
        if aligned_trades_path is None:
            aligned_trades_path = os.path.join(output_dir, 'aligned_trades.csv')

        if not os.path.exists(aligned_trades_path):
            print(f"ERROR: Aligned trades file not found: {aligned_trades_path}")
            print(f"FIX: Run step1_align_price.py first")
            return None

        print(f"  Loading aligned trades from: {os.path.basename(aligned_trades_path)}")
        trades_df = pd.read_csv(aligned_trades_path)
        trades_df['open_time'] = pd.to_datetime(trades_df['open_time'])
        trades_df['close_time'] = pd.to_datetime(trades_df['close_time'])

        print(f"  Loaded {len(trades_df)} trades\n")

        # Initialize feature matrix with trade metadata
        feature_matrix = trades_df[['trade_id', 'open_time', 'close_time', 'action', 'pips', 'profit']].copy()

        # Add auto-detected features (no candle data needed)
        print("  Computing auto-detected features...")
        feature_matrix['hour_of_day'] = trades_df['open_time'].dt.hour
        feature_matrix['day_of_week'] = trades_df['open_time'].dt.dayofweek
        feature_matrix['trade_duration_minutes'] = (trades_df['close_time'] - trades_df['open_time']).dt.total_seconds() / 60
        feature_matrix['is_winner'] = (trades_df['pips'] > 0).astype(int)

        # Handle different possible column names for direction
        if 'action' in trades_df.columns:
            # Map Buy/Sell to 1/-1
            feature_matrix['trade_direction'] = trades_df['action'].map({'Buy': 1, 'Sell': -1, 'buy': 1, 'sell': -1})
        elif 'type' in trades_df.columns:
            feature_matrix['trade_direction'] = trades_df['type'].map({'Buy': 1, 'Sell': -1, 'buy': 1, 'sell': -1})
        else:
            # Try to infer from pips and profit
            feature_matrix['trade_direction'] = 0

        print(f"    Added {5} auto-detected features")

        # Process each timeframe
        timeframes_to_process = [tf for tf in ALIGN_TIMEFRAMES if tf != 'M1' or not SKIP_M1]

        for tf in timeframes_to_process:
            print(f"\n  Processing timeframe: {tf}")

            # Check if this timeframe was aligned
            idx_col = f'{tf}_candle_idx'
            if idx_col not in trades_df.columns:
                print(f"    Skipped (not aligned)")
                continue

            # Load candle data
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')

            if not os.path.exists(candle_file):
                print(f"    Skipped (candle file not found)")
                continue

            print(f"    Loading candles from {os.path.basename(candle_file)}...", end=" ", flush=True)
            candles_df = pd.read_csv(candle_file)
            candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'])
            candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)
            print(f"({len(candles_df):,} candles)")

            # Process each trade
            print(f"    Computing indicators for {len(trades_df)} trades...")
            start_time = time.time()

            indicator_values = []

            for idx, trade in trades_df.iterrows():
                # Print progress every 50 trades
                if (idx + 1) % 50 == 0:
                    elapsed = time.time() - start_time
                    rate = (idx + 1) / elapsed
                    remaining = (len(trades_df) - (idx + 1)) / rate
                    print(f"      [{idx + 1:4d}/{len(trades_df)}] {elapsed:.0f}s elapsed, ~{remaining:.0f}s remaining")

                # Get aligned candle index
                candle_idx = trade[idx_col]

                if pd.isna(candle_idx):
                    # No alignment for this trade - fill with NaN
                    indicator_values.append({})
                    continue

                candle_idx = int(candle_idx)

                # Get lookback window (200 candles ending at the aligned candle)
                start_idx = max(0, candle_idx - LOOKBACK_CANDLES + 1)
                end_idx = candle_idx + 1

                window = candles_df.iloc[start_idx:end_idx].copy()

                if len(window) < 50:  # Need minimum candles for indicators
                    indicator_values.append({})
                    continue

                # Compute all indicators on this window
                try:
                    indicators_df = indicator_utils.compute_all_indicators(window, prefix=f'{tf}_')

                    # Take the LAST row (the values at trade entry)
                    last_indicators = indicators_df.iloc[-1].to_dict()

                    # Remove timestamp if present
                    if 'timestamp' in last_indicators:
                        del last_indicators['timestamp']

                    indicator_values.append(last_indicators)

                except Exception as e:
                    print(f"\n      WARNING: Failed to compute indicators for trade {idx}: {e}")
                    indicator_values.append({})

            # Convert indicator values to DataFrame and add to feature matrix
            indicators_df = pd.DataFrame(indicator_values)

            # Add to feature matrix
            for col in indicators_df.columns:
                feature_matrix[col] = indicators_df[col].values

            elapsed = time.time() - start_time
            print(f"    Completed in {elapsed:.0f}s ({len(indicators_df.columns)} features added)")

        # Save feature matrix
        output_file = os.path.join(output_dir, 'feature_matrix.csv')
        feature_matrix.to_csv(output_file, index=False)

        print(f"\n  Saved: {output_file}")

        # Print summary
        print(f"\n  Feature Matrix Summary:")
        print(f"    Trades:   {len(feature_matrix):,}")
        print(f"    Features: {len(feature_matrix.columns):,}")

        # Check for NaN columns
        nan_counts = feature_matrix.isna().sum()
        nan_cols = nan_counts[nan_counts > 0].sort_values(ascending=False)
        if len(nan_cols) > 0:
            print(f"    NaN values: {len(nan_cols)} columns have NaN (top 5):")
            for col, count in list(nan_cols.items())[:5]:
                pct = count / len(feature_matrix) * 100
                print(f"      {col}: {count} ({pct:.1f}%)")

        print(f"\n[STEP 2/2] COMPLETE\n")

        return feature_matrix

    except Exception as e:
        print(f"\nERROR in step2: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main entry point for command-line usage."""
    import argparse
    parser = argparse.ArgumentParser(description='Compute technical indicators for trades (multi-timeframe)')
    parser.add_argument('--aligned', type=str, help='Path to aligned trades CSV (optional)')
    parser.add_argument('--output', type=str, help='Output directory (optional)')

    args = parser.parse_args()

    result = compute_features(aligned_trades_path=args.aligned, output_dir=args.output)

    if result is None:
        sys.exit(1)


if __name__ == '__main__':
    main()

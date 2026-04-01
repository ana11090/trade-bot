"""
STEP 1 — ALIGN PRICE DATA
Aligns trade timestamps with OHLCV candle data.
For each trade, finds the exact candle that was open at the moment the trade was placed.
"""

import sys
import os
import argparse
import pandas as pd

# Add parent directory to path to import from shared
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from shared import data_utils
import state
from config_loader import load as _load_cfg

# ── Paths (always relative to this file) ─────────────────────────────────────
TRADES_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'project0_data_pipeline', 'Data Files for data mining', 'trades_clean.csv')
PRICE_DATA_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
OUTPUT_FOLDER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

# ── Configurable values (loaded from p1_config.json, fallback to defaults) ───
_cfg                 = _load_cfg()
SYMBOL               = _cfg['symbol']
BROKER_TIMEZONE      = _cfg['broker_timezone']
MIN_LOOKBACK_CANDLES = int(_cfg['min_lookback_candles'])
ALIGNMENT_TOLERANCE  = float(_cfg['alignment_tolerance_pips'])


def align_price_for_scenario(scenario):
    """
    Align trade data with price candles for a specific timeframe scenario.

    Args:
        scenario: One of 'M5', 'M15', 'H1', 'H4', 'H1_M15'

    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'=' * 60}")
    print(f"[STEP 1/7] Aligning price data — scenario: {scenario}")
    print(f"{'=' * 60}\n")

    # Determine which candle files to load based on scenario
    if scenario == 'H1_M15':
        # Combined scenario - load both H1 and M15
        timeframes = ['H1', 'M15']
    else:
        # Single timeframe scenario
        timeframes = [scenario]

    try:
        # Load trades - prefer data from Project 0 grid, fallback to CSV
        if state.loaded_data is not None:
            print("  Using trade data from Project 0 grid...")
            trades_df = data_utils.load_trades_from_state(state)
        else:
            print("  No data in Project 0 grid, loading from CSV...")
            trades_df = data_utils.load_trades_csv(TRADES_CSV_PATH)

        # Add trade_id column for tracking
        trades_df['trade_id'] = range(len(trades_df))

        # Convert trade timestamps to UTC
        trades_df = data_utils.convert_to_utc(trades_df, 'open_time', BROKER_TIMEZONE)
        trades_df = data_utils.convert_to_utc(trades_df, 'close_time', BROKER_TIMEZONE)

        # Create output directory for this scenario
        output_dir = os.path.join(OUTPUT_FOLDER, f'scenario_{scenario}')
        os.makedirs(output_dir, exist_ok=True)

        # Process each timeframe
        for tf in timeframes:
            print(f"\n--- Processing timeframe: {tf} ---")

            # Load candle data
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')

            if not os.path.exists(candle_file):
                print(f"ERROR: Candle data file not found: {candle_file}")
                print(f"FIX: Create the file with OHLCV data, or run download_price_data.py first")
                return False

            candles_df = data_utils.load_ohlcv_csv(candle_file, tf)

            # Convert candle timestamps to UTC (assume they're already in UTC from data source)
            if candles_df['timestamp'].dt.tz is None:
                candles_df['timestamp'] = candles_df['timestamp'].dt.tz_localize('UTC')

            # Align trades to candles
            aligned_trades, dropped_count = data_utils.align_trades_to_candles(
                trades_df,
                candles_df,
                MIN_LOOKBACK_CANDLES
            )

            # Verify alignment
            misaligned_count = data_utils.verify_alignment(aligned_trades, candles_df, tolerance_pips=ALIGNMENT_TOLERANCE)

            if misaligned_count > len(aligned_trades) * 0.1:  # More than 10% misaligned
                print(f"WARNING: {misaligned_count}/{len(aligned_trades)} trades appear misaligned")
                print("This could indicate timezone issues or data quality problems")

            # Add aligned candle timestamp to trades (for indicator lookup later)
            aligned_trades['aligned_candle_timestamp'] = aligned_trades['aligned_candle_idx'].apply(
                lambda idx: candles_df.iloc[int(idx)]['timestamp']
            )

            # Save aligned trades
            output_file = os.path.join(output_dir, f'trades_with_candles_{tf}.csv')
            data_utils.save_dataframe(aligned_trades, output_file, f"aligned trades for {tf}")

        print(f"\n[STEP 1/7] COMPLETE — scenario: {scenario}")
        print(f"Output directory: {output_dir}\n")

        return True

    except Exception as e:
        print(f"\nERROR in step1 — {scenario}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(description='Align trade data with price candles')
    parser.add_argument('--scenario', type=str, required=True,
                        choices=['M5', 'M15', 'H1', 'H4', 'H1_M15'],
                        help='Timeframe scenario to process')

    args = parser.parse_args()

    success = align_price_for_scenario(args.scenario)

    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()

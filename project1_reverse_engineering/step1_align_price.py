"""
STEP 1 — ALIGN PRICE DATA (Multi-Timeframe)
Aligns trade timestamps with OHLCV candle data across ALL available timeframes.
For each trade, finds the corresponding candle at M5, M15, H1, H4, and D1.
"""

import sys
import os
import pandas as pd

# Add parent directory to path to import from shared
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, PROJECT_ROOT)

from shared import data_utils
from shared.trade_history_manager import get_active_history, get_history_trades_path
from config_loader import load as _load_cfg

# ── Paths (always relative to this file) ─────────────────────────────────────
PRICE_DATA_FOLDER = os.path.join(PROJECT_ROOT, 'data')
OUTPUT_FOLDER     = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

# ── Configuration ─────────────────────────────────────────────────────────────
_cfg                 = _load_cfg()
SYMBOL               = _cfg['symbol']
ALIGN_TIMEFRAMES     = _cfg['align_timeframes'].split(',')
MIN_LOOKBACK_CANDLES = int(_cfg['min_lookback_candles'])
ALIGNMENT_TOLERANCE  = float(_cfg['alignment_tolerance_pips'])


def _get_trades_path():
    """Get trades CSV path from workspace system, fallback to legacy path."""
    active = get_active_history()
    if active:
        return get_history_trades_path(active["history_id"])
    # Fallback to legacy path
    legacy = os.path.join(PROJECT_ROOT, 'trade_histories', 'original_bot', 'trades_clean.csv')
    if os.path.exists(legacy):
        return legacy
    raise FileNotFoundError("No active trade history found. Load trades first.")


def align_all_timeframes(trades_csv_path=None, output_dir=None):
    """
    Align trades against ALL timeframes at once.
    For each trade, find the corresponding candle at each timeframe.

    Output: single CSV with columns like:
      trade_id, open_time, close_time, action, pips, profit, lots,
      M5_candle_idx, M5_candle_time,
      M15_candle_idx, M15_candle_time,
      H1_candle_idx, H1_candle_time,
      H4_candle_idx, H4_candle_time,
      D1_candle_idx, D1_candle_time

    Args:
        trades_csv_path: Optional path to trades CSV (uses workspace system if None)
        output_dir: Optional output directory (uses default if None)

    Returns:
        DataFrame with aligned trades, or None if failed
    """
    print(f"\n{'=' * 70}")
    print(f"[STEP 1/2] ALIGNING TRADES TO CANDLES (Multi-Timeframe)")
    print(f"{'=' * 70}\n")

    try:
        # Get trades path
        if trades_csv_path is None:
            trades_csv_path = _get_trades_path()

        print(f"  Loading trades from: {os.path.basename(trades_csv_path)}")

        # Load trades
        trades_df = pd.read_csv(trades_csv_path)

        # Parse timestamps (assume broker timezone, no UTC conversion)
        trades_df['open_time'] = pd.to_datetime(trades_df['open_time'])
        trades_df['close_time'] = pd.to_datetime(trades_df['close_time'])

        # Add trade_id if not present
        if 'trade_id' not in trades_df.columns:
            trades_df['trade_id'] = range(len(trades_df))

        print(f"  Loaded {len(trades_df)} trades\n")

        # Create output directory
        if output_dir is None:
            output_dir = OUTPUT_FOLDER
        os.makedirs(output_dir, exist_ok=True)

        # Process each timeframe
        aligned_counts = {}
        verified_counts = {}

        for tf in ALIGN_TIMEFRAMES:
            print(f"  Aligning to {tf}...", end=" ", flush=True)

            # Load candle data
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')

            if not os.path.exists(candle_file):
                print(f"SKIPPED (file not found)")
                continue

            candles_df = pd.read_csv(candle_file)
            candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'])

            # Sort candles by timestamp and reset index
            candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)

            # Use merge_asof to find the last candle before each trade's open_time
            # This finds the candle that was active when the trade was opened
            aligned = pd.merge_asof(
                trades_df[['trade_id', 'open_time', 'entry_price']].sort_values('open_time'),
                candles_df.reset_index().rename(columns={'index': 'candle_idx'})[['timestamp', 'candle_idx', 'high', 'low']],
                left_on='open_time',
                right_on='timestamp',
                direction='backward',
                tolerance=pd.Timedelta(days=7)  # Max gap allowed
            )

            # Add columns to main DataFrame
            trades_df[f'{tf}_candle_idx'] = aligned.set_index('trade_id')['candle_idx']
            trades_df[f'{tf}_candle_time'] = aligned.set_index('trade_id')['timestamp']

            # Count aligned trades
            aligned_count = trades_df[f'{tf}_candle_idx'].notna().sum()
            aligned_counts[tf] = aligned_count

            # Verify alignment: check if entry_price falls within candle high-low range
            # Allow some tolerance for spread and slippage
            tolerance_pips = ALIGNMENT_TOLERANCE
            pip_size = 0.01  # For XAUUSD
            tolerance = tolerance_pips * pip_size

            verified = 0
            if aligned_count > 0:
                for idx, row in aligned.iterrows():
                    if pd.notna(row['candle_idx']):
                        entry_price = row['entry_price']
                        candle_high = row['high']
                        candle_low = row['low']

                        # Check if entry price is within candle range (with tolerance)
                        if (entry_price >= candle_low - tolerance and
                            entry_price <= candle_high + tolerance):
                            verified += 1

            verified_counts[tf] = verified

            print(f"{aligned_count} trades aligned ({verified} verified)")

        # Print summary
        print(f"\n  Alignment Summary:")
        for tf in ALIGN_TIMEFRAMES:
            if tf in aligned_counts:
                align_pct = (aligned_counts[tf] / len(trades_df) * 100) if len(trades_df) > 0 else 0
                verify_pct = (verified_counts[tf] / aligned_counts[tf] * 100) if aligned_counts[tf] > 0 else 0
                print(f"    {tf:4s}: {aligned_counts[tf]:4d}/{len(trades_df)} aligned ({align_pct:5.1f}%), "
                      f"{verified_counts[tf]:4d} verified ({verify_pct:5.1f}%)")

                # Warn if verification is low
                if verify_pct < 80:
                    print(f"         WARNING: Low verification rate - possible timezone mismatch!")

        # Save aligned trades
        output_file = os.path.join(output_dir, 'aligned_trades.csv')
        trades_df.to_csv(output_file, index=False)
        print(f"\n  Saved: {output_file}")

        print(f"\n[STEP 1/2] COMPLETE\n")

        return trades_df

    except Exception as e:
        print(f"\nERROR in step1: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main entry point for command-line usage."""
    import argparse
    parser = argparse.ArgumentParser(description='Align trade data with price candles (multi-timeframe)')
    parser.add_argument('--trades', type=str, help='Path to trades CSV (optional, uses workspace if omitted)')
    parser.add_argument('--output', type=str, help='Output directory (optional)')

    args = parser.parse_args()

    result = align_all_timeframes(trades_csv_path=args.trades, output_dir=args.output)

    if result is None:
        sys.exit(1)


if __name__ == '__main__':
    main()

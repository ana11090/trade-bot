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
# WHY: Hardcoded 0.01 only applies to XAUUSD with 2-decimal pricing.
#      Reading from config lets forex (0.0001) and JPY pairs (0.01) work correctly.
# CHANGED: April 2026 — pip size from config
PIP_SIZE             = float(_cfg.get('pip_size', '0.01'))


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


def _detect_best_offset(trades_df, candles_dict, candidate_offsets=None):
    """
    Find the timezone offset (in hours) that maximizes verification rate.

    WHY: Broker server timezone often differs from candle data timezone.
         Instead of asking the user to figure it out, we try each offset
         from -12 to +12 hours and pick the one where the most trades
         have entry_price inside the matching candle's high-low range.

    Args:
        trades_df: DataFrame with 'open_time' and 'entry_price'
        candles_dict: dict of {tf: candles_df} for sampling
        candidate_offsets: list of hours to try (default: -12 to +12)

    Returns:
        Best offset in hours (int).

    CHANGED: April 2026 — auto-detect timezone offset
    """
    if candidate_offsets is None:
        candidate_offsets = list(range(-12, 13))  # -12 to +12 hours

    # Use H1 for the detection (faster than M5, more precise than D1)
    detect_tf = 'H1' if 'H1' in candles_dict else list(candles_dict.keys())[0]
    detect_candles = candles_dict[detect_tf]

    if detect_candles is None or len(detect_candles) == 0:
        print(f"    No {detect_tf} candles for offset detection — using offset 0")
        return 0

    # Sample up to 200 trades for speed (more than enough to detect offset)
    sample_size = min(200, len(trades_df))
    sample = trades_df.sample(n=sample_size, random_state=42) if len(trades_df) > sample_size else trades_df

    print(f"    Auto-detecting timezone offset (testing {len(candidate_offsets)} offsets on {len(sample)} trades)...")

    best_offset = 0
    best_verified = 0
    results = []

    candles_sorted = detect_candles.sort_values('timestamp')

    for offset_hours in candidate_offsets:
        shifted = sample.copy()
        shifted['open_time'] = pd.to_datetime(shifted['open_time']) + pd.Timedelta(hours=offset_hours)

        try:
            shifted_sorted = shifted.sort_values('open_time')

            merged = pd.merge_asof(
                shifted_sorted[['open_time', 'entry_price']],
                candles_sorted[['timestamp', 'high', 'low']],
                left_on='open_time',
                right_on='timestamp',
                direction='backward',
            )

            # Count how many trades have entry_price in [low, high]
            tolerance = ALIGNMENT_TOLERANCE * PIP_SIZE
            in_range = (
                (merged['entry_price'] >= merged['low'] - tolerance) &
                (merged['entry_price'] <= merged['high'] + tolerance)
            )
            verified = int(in_range.sum())
            results.append((offset_hours, verified))

            if verified > best_verified:
                best_verified = verified
                best_offset = offset_hours
        except Exception:
            results.append((offset_hours, 0))
            continue

    # Print top 5 offsets
    results.sort(key=lambda x: x[1], reverse=True)
    print(f"    Top offsets (verified count out of {len(sample)}):")
    for off, ver in results[:5]:
        marker = " <- BEST" if off == best_offset else ""
        pct = ver / len(sample) * 100
        print(f"      Offset {off:+d}h: {ver:3d} ({pct:5.1f}%){marker}")

    print(f"    -> Using offset: {best_offset:+d} hours")
    return best_offset


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

        # Normalize column names (handle different CSV formats)
        column_mapping = {
            'Open Date': 'open_time',
            'Close Date': 'close_time',
            'Open Price': 'entry_price',
            'Close Price': 'exit_price',
            'Action': 'action',
            'Lots': 'lots',
            'Pips': 'pips',
            'Profit': 'profit',
        }
        trades_df.rename(columns=column_mapping, inplace=True)

        # Parse timestamps (assume broker timezone, no UTC conversion)
        # Use dayfirst=True for DD/MM/YYYY format, format='mixed' to handle inconsistent formats
        trades_df['open_time'] = pd.to_datetime(trades_df['open_time'], format='mixed', dayfirst=True)
        trades_df['close_time'] = pd.to_datetime(trades_df['close_time'], format='mixed', dayfirst=True)

        # Add trade_id if not present
        if 'trade_id' not in trades_df.columns:
            trades_df['trade_id'] = range(len(trades_df))

        print(f"  Loaded {len(trades_df)} trades\n")

        # ── AUTO-DETECT TIMEZONE OFFSET ───────────────────────────────────
        # WHY: Broker server timezone often differs from candle CSV timezone.
        #      Try every offset and pick the one with the best verification.
        # CHANGED: April 2026 — auto-detect timezone
        _candles_for_detection = {}
        for _tf in ['H1']:
            _candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{_tf}.csv')
            if os.path.exists(_candle_file):
                try:
                    _cdf = pd.read_csv(_candle_file)
                    if 'timestamp' not in _cdf.columns:
                        for _col in _cdf.columns:
                            if _col.lower() in ('time', 'date', 'datetime', 'open_time'):
                                _cdf = _cdf.rename(columns={_col: 'timestamp'})
                                break
                    _cdf['timestamp'] = pd.to_datetime(_cdf['timestamp'])
                    _candles_for_detection[_tf] = _cdf
                except Exception:
                    pass

        if _candles_for_detection and 'entry_price' in trades_df.columns:
            detected_offset = _detect_best_offset(trades_df, _candles_for_detection)
            if detected_offset != 0:
                print(f"    Applying timezone offset {detected_offset:+d}h to trade timestamps")
                trades_df['open_time'] = trades_df['open_time'] + pd.Timedelta(hours=detected_offset)
                trades_df['close_time'] = trades_df['close_time'] + pd.Timedelta(hours=detected_offset)
        else:
            print(f"    Could not load candles for offset detection — proceeding without offset")

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
            # WHY: merge_asof can produce duplicate trade_id rows if two candles
            #      have identical timestamps. drop_duplicates keeps the first
            #      match (closest candle) and prevents index ambiguity.
            # CHANGED: April 2026 — drop_duplicates before set_index
            aligned_dedup = aligned.drop_duplicates(subset=['trade_id'])
            trades_df[f'{tf}_candle_idx']  = aligned_dedup.set_index('trade_id')['candle_idx']
            trades_df[f'{tf}_candle_time'] = aligned_dedup.set_index('trade_id')['timestamp']

            # Count aligned trades
            aligned_count = trades_df[f'{tf}_candle_idx'].notna().sum()
            aligned_counts[tf] = aligned_count

            # Verify alignment: check if entry_price falls within candle high-low range
            # Allow some tolerance for spread and slippage
            tolerance = ALIGNMENT_TOLERANCE * PIP_SIZE

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

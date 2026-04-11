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

from shared import indicator_utils
from config_loader import load as _load_cfg

# CHANGED: April 2026 — UI-safe logging (Phase 19d)
from shared.logging_setup import get_logger
log = get_logger(__name__)

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
    log.info(f"\n{'=' * 70}")
    log.info(f"[STEP 2/2] COMPUTING INDICATORS (Multi-Timeframe)")
    log.info(f"{'=' * 70}\n")

    try:
        # Get paths
        if output_dir is None:
            output_dir = OUTPUT_FOLDER
        if aligned_trades_path is None:
            aligned_trades_path = os.path.join(output_dir, 'aligned_trades.csv')

        if not os.path.exists(aligned_trades_path):
            log.error(f" Aligned trades file not found: {aligned_trades_path}")
            log.info(f"FIX: Run step1_align_price.py first")
            return None

        log.info(f"  Loading aligned trades from: {os.path.basename(aligned_trades_path)}")
        trades_df = pd.read_csv(aligned_trades_path)
        trades_df['open_time'] = pd.to_datetime(trades_df['open_time'])
        trades_df['close_time'] = pd.to_datetime(trades_df['close_time'])

        log.info(f"  Loaded {len(trades_df)} trades\n")

        # Initialize feature matrix with trade metadata
        feature_matrix = trades_df[['trade_id', 'open_time', 'close_time', 'action', 'pips', 'profit']].copy()

        # Add auto-detected features (no candle data needed)
        log.info("  Computing auto-detected features...")
        # WHY: Only safe time-based features go into the feature matrix.
        #      is_winner IS the target → leakage. trade_duration_minutes
        #      leaks because winning trades naturally run longer than losers.
        #      Both are stored nowhere here; step3_label_trades.py adds the
        #      proper `outcome` target column separately.
        # CHANGED: April 2026 — defense-in-depth against leakage
        feature_matrix['hour_of_day'] = trades_df['open_time'].dt.hour
        feature_matrix['day_of_week'] = trades_df['open_time'].dt.dayofweek
        # NOTE: trade_duration_minutes and is_winner are NOT added here.
        # If you need them for analysis, compute them from trades_df at point of use.

        # Handle different possible column names for direction
        if 'action' in trades_df.columns:
            # Map Buy/Sell to 1/-1
            feature_matrix['trade_direction'] = trades_df['action'].map({'Buy': 1, 'Sell': -1, 'buy': 1, 'sell': -1})
        elif 'type' in trades_df.columns:
            feature_matrix['trade_direction'] = trades_df['type'].map({'Buy': 1, 'Sell': -1, 'buy': 1, 'sell': -1})
        else:
            # Try to infer from pips and profit
            feature_matrix['trade_direction'] = 0

        log.info(f"    Added {5} auto-detected features")

        # Process each timeframe
        timeframes_to_process = [tf for tf in ALIGN_TIMEFRAMES if tf != 'M1' or not SKIP_M1]

        for tf in timeframes_to_process:
            log.info(f"\n  Processing timeframe: {tf}")

            # Check if this timeframe was aligned
            idx_col = f'{tf}_candle_idx'
            if idx_col not in trades_df.columns:
                log.info(f"    Skipped (not aligned)")
                continue

            # Load candle data
            candle_file = os.path.join(PRICE_DATA_FOLDER, f'{SYMBOL.lower()}_{tf}.csv')

            if not os.path.exists(candle_file):
                log.info(f"    Skipped (candle file not found)")
                continue

            # WHY: Cannot use logging here — end=" " creates inline progress
            #      (e.g., "Loading... (1,234 candles)") which logging always breaks
            #      with newlines. Keep print() for UX.
            # PRESERVED: April 2026 — Phase 19d Fix 3
            print(f"    Loading candles from {os.path.basename(candle_file)}...", end=" ", flush=True)
            candles_df = pd.read_csv(candle_file)
            candles_df['timestamp'] = pd.to_datetime(candles_df['timestamp'])
            candles_df = candles_df.sort_values('timestamp').reset_index(drop=True)
            log.info(f"({len(candles_df):,} candles)")

            # ── COMPUTE INDICATORS ONCE ON FULL DATAFRAME ─────────────────
            # WHY: The old code computed indicators in a loop — for every trade
            #      it sliced a 200-candle window and recomputed 80 indicators.
            #      For 10,000 trades that's 800 MILLION wasted calculations.
            #
            #      The fix: compute ALL indicators once on the entire candle
            #      dataframe (pandas/numpy vectorized), then look up the value
            #      at each trade's aligned candle index. Same exact results,
            #      just done in one shot instead of 10,000 separate slices.
            # CHANGED: April 2026 — 50-200x speedup for step 2
            log.info(f"    Computing all indicators ONCE on {len(candles_df)} candles...")
            t0 = time.time()

            try:
                full_indicators_df = indicator_utils.compute_all_indicators(
                    candles_df, prefix=f'{tf}_'
                )
                t1 = time.time()
                log.info(f"    Computed {len(full_indicators_df.columns)} indicators in {t1-t0:.1f}s")
            except Exception as e:
                log.error(f"     computing indicators: {e}")
                # Fall back: empty indicators for all trades
                indicator_values = [{} for _ in range(len(trades_df))]
                # Convert indicator values to DataFrame and add to feature matrix
                indicators_df = pd.DataFrame(indicator_values)
                for col in indicators_df.columns:
                    feature_matrix[col] = indicators_df[col].values
                continue

            # Now look up each trade's row from the precomputed dataframe
            # WHY: This is just a row index lookup — milliseconds for thousands of trades.
            log.info(f"    Looking up indicator values for {len(trades_df)} trades...")
            t0 = time.time()

            indicator_values = []
            indicator_cols = [c for c in full_indicators_df.columns if c != 'timestamp']

            for idx, trade in trades_df.iterrows():
                candle_idx = trade[idx_col]

                if pd.isna(candle_idx):
                    indicator_values.append({})
                    continue

                candle_idx = int(candle_idx)

                # Need at least LOOKBACK_CANDLES of history for indicators to be valid
                # WHY: Hardcoded 50 was less than LOOKBACK_CANDLES (often 200).
                #      Rows with fewer than LOOKBACK_CANDLES preceding candles
                #      produce NaN-heavy indicators that pollute the feature matrix.
                # CHANGED: April 2026 — use config LOOKBACK_CANDLES
                if candle_idx < LOOKBACK_CANDLES or candle_idx >= len(full_indicators_df):
                    indicator_values.append({})
                    continue

                try:
                    # Direct row lookup — O(1) operation
                    row = full_indicators_df.iloc[candle_idx]
                    last_indicators = {col: row[col] for col in indicator_cols}
                    indicator_values.append(last_indicators)
                except Exception:
                    indicator_values.append({})

            t1 = time.time()
            log.info(f"    Lookup complete in {t1-t0:.1f}s")

            # Convert indicator values to DataFrame and add to feature matrix
            indicators_df = pd.DataFrame(indicator_values)

            # Add to feature matrix
            for col in indicators_df.columns:
                feature_matrix[col] = indicators_df[col].values

            log.info(f"    Completed ({len(indicators_df.columns)} features added)")

        # Save feature matrix
        output_file = os.path.join(output_dir, 'feature_matrix.csv')
        feature_matrix.to_csv(output_file, index=False)

        log.info(f"\n  Saved: {output_file}")

        # Print summary
        log.info(f"\n  Feature Matrix Summary:")
        log.info(f"    Trades:   {len(feature_matrix):,}")
        log.info(f"    Features: {len(feature_matrix.columns):,}")

        # Check for NaN columns
        nan_counts = feature_matrix.isna().sum()
        nan_cols = nan_counts[nan_counts > 0].sort_values(ascending=False)
        if len(nan_cols) > 0:
            log.info(f"    NaN values: {len(nan_cols)} columns have NaN (top 5):")
            for col, count in list(nan_cols.items())[:5]:
                pct = count / len(feature_matrix) * 100
                log.info(f"      {col}: {count} ({pct:.1f}%)")

        log.info(f"\n[STEP 2/2] COMPLETE\n")

        return feature_matrix

    except Exception as e:
        log.error(f"\n in step2: {str(e)}")
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

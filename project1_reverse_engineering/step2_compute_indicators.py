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
        # WHY (Phase 52 Fix 3): Old code put 'pips' and 'profit' (the
        #      training targets) directly into the feature matrix
        #      under their natural names. Any script training "all
        #      columns except is_winner" would silently include these
        #      and leak the target. The leak was prevented only by
        #      analyze.py's LEAK_COLS set; other consumers had no
        #      protection. Rename to _LEAK_pips and _LEAK_profit so
        #      naive trainers either drop them by prefix or break
        #      loudly when looking for the original names.
        # CHANGED: April 2026 — Phase 52 Fix 3 — leak-prefix target columns
        #          (audit Part D MED #38)
        feature_matrix = trades_df[['trade_id', 'open_time', 'close_time', 'action']].copy()
        feature_matrix['_LEAK_pips']   = trades_df['pips']
        feature_matrix['_LEAK_profit'] = trades_df['profit']

        # Add auto-detected features (no candle data needed)
        log.info("  Computing auto-detected features...")
        # WHY: Only safe time-based features go into the feature matrix.
        #      is_winner IS the target → leakage. trade_duration_minutes
        #      leaks because winning trades naturally run longer than losers.
        #      Both are stored nowhere here; step3_label_trades.py adds the
        #      proper `outcome` target column separately.
        # CHANGED: April 2026 — defense-in-depth against leakage
        # WHY (Phase 60 Fix 1b): Old code stored broker-time hours.
        #      The live EA session features use TimeGMT() (UTC). A rule
        #      trained on "hour_of_day >= 13" (London/NY overlap) fired
        #      at UTC 13 in training but at broker-time 13 (≈ UTC 11 for
        #      EET) in live — two hours apart. Normalise hour_of_day to
        #      UTC by subtracting the configured utc_offset_hours so
        #      training and live evaluate session features on the same scale.
        # CHANGED: April 2026 — Phase 60 Fix 1b — UTC-normalised hour_of_day
        #          (audit Part D HIGH #7)
        _utc_offset = int(_cfg.get('utc_offset_hours', '2'))
        feature_matrix['hour_of_day'] = (
            (trades_df['open_time'].dt.hour - _utc_offset) % 24
        )
        feature_matrix['day_of_week'] = trades_df['open_time'].dt.dayofweek
        # NOTE: trade_duration_minutes and is_winner are NOT added here.
        # If you need them for analysis, compute them from trades_df at point of use.

        # WHY (Phase 57 Fix 2): Old map only handled 'Buy'/'Sell'/'buy'/'sell'.
        #      Brokers export trade type in many spellings: Long/Short,
        #      BUY_LIMIT/SELL_LIMIT, 'Buy Limit'/'Sell Stop', etc.
        #      Unmapped values became NaN, which fillna(median)=0 converted to
        #      "neutral" — silent direction leakage into the feature matrix.
        #      Expand the map to cover all known MT5/broker variants.
        #      Remaining unknowns are inferred from pips sign, then 0.
        # CHANGED: April 2026 — Phase 57 Fix 2 — expanded direction map
        #          (audit Part D MEDIUM #37)
        _DIR_MAP = {
            # Standard MT5 spellings
            'Buy': 1, 'Sell': -1, 'buy': 1, 'sell': -1,
            'BUY': 1, 'SELL': -1,
            # Positional / prop-firm exports
            'Long': 1, 'Short': -1, 'long': 1, 'short': -1,
            'LONG': 1, 'SHORT': -1,
            # Pending order labels (direction still applies to the fill)
            'Buy Limit': 1,  'Sell Limit': -1,
            'Buy Stop':  1,  'Sell Stop':  -1,
            'BUY_LIMIT': 1,  'SELL_LIMIT': -1,
            'BUY_STOP':  1,  'SELL_STOP':  -1,
        }
        _src_col = None
        if 'action' in trades_df.columns:
            _src_col = trades_df['action']
        elif 'type' in trades_df.columns:
            _src_col = trades_df['type']

        if _src_col is not None:
            _mapped = _src_col.map(_DIR_MAP)
            # Infer remaining NaN from pips sign if available
            if _mapped.isna().any() and 'pips' in trades_df.columns:
                _pip_sign = np.sign(trades_df['pips'].fillna(0)).replace(0, np.nan)
                _mapped = _mapped.fillna(_pip_sign)
            feature_matrix['trade_direction'] = _mapped.fillna(0).astype(int)
        else:
            feature_matrix['trade_direction'] = 0

        log.info(f"    Added {5} auto-detected features")

        # Process each timeframe
        # WHY (Phase 52 Fix 1): Old code silently dropped M1 from the
        #      processing list when SKIP_M1 was True (default). A user
        #      who deliberately added M1 to align_timeframes got no
        #      M1 features and no log explaining why. Warn loudly so
        #      the user knows their M1 config was overridden.
        # CHANGED: April 2026 — Phase 52 Fix 1 — visible M1 skip
        #          (audit Part D MED #36)
        if 'M1' in ALIGN_TIMEFRAMES and SKIP_M1:
            log.warning(
                "  [STEP2] M1 is in align_timeframes but skip_m1_features=true. "
                "M1 will be EXCLUDED from the feature matrix. Set "
                "skip_m1_features=false in p1_config.json to include M1."
            )
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
                # WHY (Phase 58 Fix 3): compute_all_indicators may reset or
                #      reindex the DataFrame internally. iloc[candle_idx]
                #      below is positional — if the index is not a clean
                #      0..N-1 range the wrong row is fetched silently.
                #      Force-reset the index here so the positional lookup
                #      is always correct.
                # CHANGED: April 2026 — Phase 58 Fix 3 — explicit index reset
                #          (audit Part D HIGH #35)
                full_indicators_df = full_indicators_df.reset_index(drop=True)
                t1 = time.time()
                log.info(f"    Computed {len(full_indicators_df.columns)} indicators in {t1-t0:.1f}s")
            except Exception as e:
                # WHY (Phase 58 Fix 2): Old error was only logged at ERROR
                #      level — the GUI never shows it, so users had no idea
                #      why an entire TF's features were missing from their
                #      analysis. Promote to a prominent WARNING with the TF
                #      name and exception, and track which TFs failed so
                #      run_analysis can surface them in the report.
                # CHANGED: April 2026 — Phase 58 Fix 2 — visible TF failure
                #          (audit Part D HIGH #34)
                import traceback as _tb
                log.error(
                    f"\n{'='*60}\n"
                    f"  STEP 2 ERROR — {tf} indicators FAILED\n"
                    f"  All {tf}_* features will be MISSING from the\n"
                    f"  feature matrix. This TF cannot be analysed.\n"
                    f"  Exception: {e}\n"
                    f"  {_tb.format_exc().strip()}\n"
                    f"{'='*60}\n"
                )
                # Track failed TFs at module level so callers can warn users
                if not hasattr(compute_features, '_failed_tfs'):
                    compute_features._failed_tfs = []
                compute_features._failed_tfs.append(tf)
                # No features added — just skip this TF
                continue

            # Now look up each trade's row from the precomputed dataframe
            # WHY: This is just a row index lookup — milliseconds for thousands of trades.
            log.info(f"    Looking up indicator values for {len(trades_df)} trades...")
            t0 = time.time()

            indicator_values = []
            indicator_cols = [c for c in full_indicators_df.columns if c != 'timestamp']

            # WHY (Phase 45 Fix 1): Old code silently appended {} for trades
            #      whose candle_idx was below LOOKBACK_CANDLES. User lost the
            #      first N trades per TF with no log explaining why. Track
            #      the count and log a summary so the user knows how many
            #      trades were warmup-dropped per timeframe.
            # CHANGED: April 2026 — Phase 45 Fix 1 — visible warmup drops
            #          (audit Part D HIGH #33)
            _warmup_drops = 0
            _nan_drops    = 0
            _oob_drops    = 0

            for idx, trade in trades_df.iterrows():
                candle_idx = trade[idx_col]

                if pd.isna(candle_idx):
                    indicator_values.append({})
                    _nan_drops += 1
                    continue

                candle_idx = int(candle_idx)

                # Need at least LOOKBACK_CANDLES of history for indicators to be valid
                if candle_idx < LOOKBACK_CANDLES:
                    indicator_values.append({})
                    _warmup_drops += 1
                    continue
                if candle_idx >= len(full_indicators_df):
                    indicator_values.append({})
                    _oob_drops += 1
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
            # Phase 45 Fix 1b: warmup-drop visibility
            if _warmup_drops + _nan_drops + _oob_drops > 0:
                log.warning(
                    f"    [STEP2] {tf}: dropped {_warmup_drops} trades to warmup "
                    f"(< {LOOKBACK_CANDLES} preceding candles), "
                    f"{_nan_drops} to missing candle_idx, "
                    f"{_oob_drops} to out-of-range candle_idx"
                )

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

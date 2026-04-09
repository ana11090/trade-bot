"""
Tick-to-Candle Pipeline — converts raw tick data into OHLCV candles.

Reads monthly tick files from a configurable root folder, aggregates into
candles at multiple timeframes, applies ×100 price scaling, and saves
clean CSV files to the project's data/ folder.

Usage:
    python build_candles_from_ticks.py --tick-root "D:\\traiding data\\trade-bot\\xauusd\\ticks"

    Or edit TICK_ROOT below and just run:
    python build_candles_from_ticks.py
"""

import argparse
import os
import sys
import shutil
import pandas as pd
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────────────────
# WHY: Hardcoded absolute path to a single developer's machine broke the script
#      for everyone else. The --tick-root argument already works, but callers
#      that import TICK_ROOT directly (CI scripts, tests) would get the wrong
#      path. Using os.environ.get lets users set TICK_ROOT in their environment
#      without editing source. Falls back to xauusd/ticks/ next to this file.
# CHANGED: April 2026 — env-var override for TICK_ROOT (audit LOW)
TICK_ROOT = os.environ.get(
    'TICK_ROOT',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xauusd', 'ticks')
)

# Price scaling factor (tick data prices × this = real USD prices)
PRICE_SCALE = 100.0

# Output folder (relative to this script's location)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Symbol
SYMBOL = "xauusd"

# Timeframes to generate
TIMEFRAMES = {
    "M1":  "1min",     # pandas resample rule
    "M5":  "5min",
    "M15": "15min",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1D",
    "W1":  "1W",
    "MN":  "1ME",      # Month End
}

# Price column to use from tick data for OHLC
# "mid" = (ask + bid) / 2, which is the standard for candle building
PRICE_COLUMN = "mid"


def discover_tick_files(tick_root):
    """
    Scan the tick root folder for all tick CSV files.
    Returns a sorted list of (year, month, file_path) tuples.

    Expected structure: {tick_root}/{YYYY}/ticks_{YYYY}_{MM}.csv
    """
    files = []

    if not os.path.isdir(tick_root):
        print(f"ERROR: Tick root not found: {tick_root}")
        return files

    # Scan year folders
    for year_folder in sorted(os.listdir(tick_root)):
        year_path = os.path.join(tick_root, year_folder)
        if not os.path.isdir(year_path):
            continue
        try:
            year = int(year_folder)
        except ValueError:
            continue

        # Scan tick files in this year folder
        for fname in sorted(os.listdir(year_path)):
            if not fname.endswith(".csv"):
                continue
            # Parse filename: ticks_YYYY_MM.csv
            parts = fname.replace(".csv", "").split("_")
            if len(parts) >= 3 and parts[0] == "ticks":
                try:
                    file_year = int(parts[1])
                    file_month = int(parts[2])
                    files.append((file_year, file_month, os.path.join(year_path, fname)))
                except ValueError:
                    # Try alternative patterns
                    files.append((year, 0, os.path.join(year_path, fname)))

    files.sort()
    return files


def process_tick_file(file_path, price_scale=100.0, price_col="mid"):
    """
    Read a single tick CSV file and aggregate into candles for all timeframes.

    Returns dict: {timeframe_name: DataFrame with columns [timestamp, open, high, low, close, volume]}

    Memory-efficient: reads the file, processes, and returns aggregated data.
    The original tick data is not kept in memory after processing.
    """
    print(f"  Reading: {os.path.basename(file_path)}...", end=" ", flush=True)

    # Read tick file
    try:
        df = pd.read_csv(file_path)
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    print(f"({len(df):,} ticks)", end=" ", flush=True)

    # Parse timestamp and set as index
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.set_index("timestamp").sort_index()

    # Scale prices
    for col in ["ask", "bid", "mid"]:
        if col in df.columns:
            df[col] = df[col] * price_scale

    # Use the configured price column for OHLC
    if price_col not in df.columns:
        print(f"ERROR: Column '{price_col}' not found")
        return None

    price = df[price_col]
    vol = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

    # Aggregate into each timeframe
    results = {}
    for tf_name, resample_rule in TIMEFRAMES.items():
        try:
            ohlc = price.resample(resample_rule).agg(
                open="first",
                high="max",
                low="min",
                close="last"
            )
            v = vol.resample(resample_rule).sum()

            candles = ohlc.copy()
            candles["volume"] = v

            # Drop rows where all OHLC are NaN (no ticks in that period)
            candles = candles.dropna(subset=["open", "high", "low", "close"])

            # Reset index to get timestamp as a column
            candles = candles.reset_index()
            candles.rename(columns={"index": "timestamp"}, inplace=True)
            if "timestamp" not in candles.columns and candles.index.name == "timestamp":
                candles = candles.reset_index()

            # Ensure timestamp column exists and is named correctly
            if len(candles.columns) > 0 and candles.columns[0] != "timestamp":
                candles = candles.rename(columns={candles.columns[0]: "timestamp"})

            results[tf_name] = candles
        except Exception as e:
            print(f"\n  WARNING: Failed to build {tf_name}: {e}")

    print("OK")
    return results


def build_all_candles(tick_root, output_dir, price_scale=100.0, price_col="mid"):
    """
    Main pipeline: discover files, process each one, merge results, save.
    """
    print("=" * 70)
    print("TICK-TO-CANDLE PIPELINE")
    print(f"  Tick root:    {tick_root}")
    print(f"  Output dir:   {output_dir}")
    print(f"  Price scale:  ×{price_scale}")
    print(f"  Price column: {price_col}")
    print(f"  Timeframes:   {', '.join(TIMEFRAMES.keys())}")
    print("=" * 70)

    # Discover files
    files = discover_tick_files(tick_root)
    if not files:
        print("ERROR: No tick files found!")
        return

    print(f"\nFound {len(files)} tick files:")
    years = sorted(set(y for y, m, p in files))
    for year in years:
        months = [m for y, m, p in files if y == year]
        print(f"  {year}: {len(months)} months ({min(months)}-{max(months)})")
    print()

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Back up existing candle files
    backup_dir = os.path.join(output_dir, "_backup_old")
    backup_created = False
    if os.path.exists(output_dir):
        for f in os.listdir(output_dir):
            if f.endswith(".csv") and f.startswith(SYMBOL):
                if not backup_created:
                    os.makedirs(backup_dir, exist_ok=True)
                    print("Backing up existing candle files...")
                    backup_created = True
                src = os.path.join(output_dir, f)
                dst = os.path.join(backup_dir, f)
                shutil.copy2(src, dst)
                print(f"  Backed up: {f}")
    if backup_created:
        print()

    # Process each file and accumulate candles
    # Use dict of lists, then concat at the end per timeframe
    accumulated = {tf: [] for tf in TIMEFRAMES}

    total_files = len(files)

    for idx, (year, month, file_path) in enumerate(files):
        print(f"[{idx+1}/{total_files}] {year}-{month:02d}")

        result = process_tick_file(file_path, price_scale, price_col)
        if result is None:
            continue

        for tf_name, candles_df in result.items():
            if len(candles_df) > 0:
                accumulated[tf_name].append(candles_df)

    # Merge and save each timeframe
    print()
    print("=" * 70)
    print("SAVING CANDLE FILES")
    print("=" * 70)

    for tf_name in TIMEFRAMES:
        if not accumulated[tf_name]:
            print(f"  {tf_name}: No data — skipping")
            continue

        # Concatenate all months
        combined = pd.concat(accumulated[tf_name], ignore_index=True)

        # Sort by timestamp and remove duplicates
        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"])
        combined = combined.reset_index(drop=True)

        # Round prices to 2 decimal places (USD cents)
        for col in ["open", "high", "low", "close"]:
            combined[col] = combined[col].round(2)

        # Save
        output_file = os.path.join(output_dir, f"{SYMBOL}_{tf_name}.csv")
        combined.to_csv(output_file, index=False)

        date_range = f"{combined['timestamp'].iloc[0]} to {combined['timestamp'].iloc[-1]}"
        print(f"  {tf_name:4s}: {len(combined):>8,} candles | {date_range}")

    print()
    print("=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Convert tick data to OHLCV candles")
    parser.add_argument("--tick-root", default=TICK_ROOT,
                        help=f"Path to tick data root folder (default: {TICK_ROOT})")
    parser.add_argument("--output", default=OUTPUT_DIR,
                        help=f"Output folder for candle CSVs (default: {OUTPUT_DIR})")
    parser.add_argument("--scale", type=float, default=PRICE_SCALE,
                        help=f"Price scaling factor (default: {PRICE_SCALE})")
    parser.add_argument("--price-col", default=PRICE_COLUMN,
                        help=f"Tick column for OHLC (default: {PRICE_COLUMN})")
    args = parser.parse_args()

    build_all_candles(args.tick_root, args.output, args.scale, args.price_col)


if __name__ == "__main__":
    main()

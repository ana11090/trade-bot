"""
Create OHLCV timeframe files from tick data for project1_reverse_engineering.

Reads tick data from xauusd/ticks/ and creates aggregated OHLCV candle files
for M5, M15, H1, H4, D1 timeframes in the data/ folder.
"""

import pandas as pd
import os
from datetime import datetime
import glob

# Configuration
TICK_DATA_PATH = 'xauusd/ticks/'
OUTPUT_PATH = 'data/'
SYMBOL = 'XAUUSD'

# Timeframes to create
TIMEFRAMES = {
    'M5': '5min',   # 5 minutes
    'M15': '15min', # 15 minutes
    'H1': '1H',     # 1 hour
    'H4': '4H',     # 4 hours
    'D1': '1D',     # 1 day
    'W1': '1W',     # 1 week
    'MN': '1ME',    # 1 month (month end)
}

def get_all_tick_files():
    """
    Get list of all tick data CSV files.
    Returns sorted list of file paths.
    """
    print(f"Scanning tick data files in {TICK_DATA_PATH}...")

    all_files = []
    for year_dir in sorted(os.listdir(TICK_DATA_PATH)):
        year_path = os.path.join(TICK_DATA_PATH, year_dir)
        if os.path.isdir(year_path):
            pattern = os.path.join(year_path, 'ticks_*.csv')
            files = glob.glob(pattern)
            all_files.extend(sorted(files))

    print(f"Found {len(all_files)} tick files")
    return all_files

def process_tick_file_to_ohlcv(file_path, resample_rule):
    """
    Process a single tick file into OHLCV candles.

    Args:
        file_path: Path to tick CSV file
        resample_rule: Pandas resample rule (e.g., '5T' for 5 minutes)

    Returns:
        DataFrame with OHLCV candles for this file
    """
    df = pd.read_csv(file_path)
    # Handle malformed timestamps by coercing errors to NaT
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    # Drop rows with invalid timestamps
    df = df.dropna(subset=['timestamp'])
    df = df.set_index('timestamp')

    # Use mid price for OHLC
    df['price'] = df['mid']

    # Resample to OHLCV
    ohlcv = pd.DataFrame()
    ohlcv['open'] = df['price'].resample(resample_rule).first()
    ohlcv['high'] = df['price'].resample(resample_rule).max()
    ohlcv['low'] = df['price'].resample(resample_rule).min()
    ohlcv['close'] = df['price'].resample(resample_rule).last()
    ohlcv['volume'] = df['volume'].resample(resample_rule).sum()

    # Reset index
    ohlcv = ohlcv.reset_index()

    # Drop rows with NaN
    ohlcv = ohlcv.dropna()

    return ohlcv

def create_ohlcv_from_files(tick_files, timeframe_name, resample_rule):
    """
    Create OHLCV candles from tick files, processing in batches.

    Args:
        tick_files: List of tick CSV file paths
        timeframe_name: Name for output file (e.g., 'M5')
        resample_rule: Pandas resample rule (e.g., '5T' for 5 minutes)

    Returns:
        DataFrame with OHLCV candles
    """
    print(f"\nCreating {timeframe_name} candles (rule: {resample_rule})...")

    all_candles = []

    for i, file in enumerate(tick_files):
        if i % 20 == 0:
            print(f"  Processing file {i+1}/{len(tick_files)}: {os.path.basename(file)}")

        candles = process_tick_file_to_ohlcv(file, resample_rule)
        if len(candles) > 0:
            all_candles.append(candles)

    # Combine all candles
    print(f"  Combining candles from {len(all_candles)} files...")
    combined = pd.concat(all_candles, ignore_index=True)

    # Sort by timestamp and remove duplicates
    combined = combined.sort_values('timestamp').reset_index(drop=True)

    print(f"  Created {len(combined):,} candles")
    print(f"  Date range: {combined['timestamp'].min()} to {combined['timestamp'].max()}")

    return combined

def save_candles(candles_df, timeframe_name):
    """
    Save OHLCV candles to CSV file.
    """
    os.makedirs(OUTPUT_PATH, exist_ok=True)

    output_file = os.path.join(OUTPUT_PATH, f'{SYMBOL.lower()}_{timeframe_name}.csv')

    print(f"  Saving to {output_file}...")
    candles_df.to_csv(output_file, index=False)

    # Show file size
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"  File size: {size_mb:.1f} MB")

def main():
    """
    Main function to create all timeframe files.
    """
    print("=" * 60)
    print("CREATING TIMEFRAME FILES FROM TICK DATA")
    print("=" * 60)

    # Get all tick files
    tick_files = get_all_tick_files()

    # Create and save each timeframe
    for tf_name, resample_rule in TIMEFRAMES.items():
        candles = create_ohlcv_from_files(tick_files, tf_name, resample_rule)
        save_candles(candles, tf_name)

    print("\n" + "=" * 60)
    print("DONE - All timeframe files created successfully!")
    print("=" * 60)
    print(f"\nFiles saved to {OUTPUT_PATH}:")
    for tf_name in TIMEFRAMES.keys():
        filename = f'{SYMBOL.lower()}_{tf_name}.csv'
        print(f"  - {filename}")

if __name__ == '__main__':
    main()

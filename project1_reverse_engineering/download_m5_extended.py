"""
Download Extended M5 Data in Chunks
Downloads M5 data in multiple chunks to get maximum history
"""

import os
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime

SYMBOL = 'XAUUSD'
OUTPUT_FOLDER = '../data/'
CHUNK_SIZE = 50000  # Download 50k bars at a time

def main():
    print("\n" + "=" * 60)
    print("  DOWNLOADING EXTENDED M5 DATA")
    print("=" * 60 + "\n")

    # Initialize MT5
    if not mt5.initialize():
        print("[ERROR] Failed to initialize MT5")
        return

    print("[OK] Connected to MT5")

    # Download multiple chunks
    all_data = []
    offset = 0
    chunk_num = 1

    while True:
        print(f"Chunk {chunk_num}: Downloading {CHUNK_SIZE:,} bars (offset {offset:,})...", end=" ", flush=True)

        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, offset, CHUNK_SIZE)

        if rates is None or len(rates) == 0:
            print("[DONE] No more data available")
            break

        # Convert to DataFrame
        df = pd.DataFrame(rates)
        df['timestamp'] = pd.to_datetime(df['time'], unit='s')

        print(f"[OK] Got {len(rates):,} bars | Range: {df['timestamp'].min()} to {df['timestamp'].max()}")

        all_data.append(df)

        # If we got less than requested, we've reached the end
        if len(rates) < CHUNK_SIZE:
            print("[DONE] Reached end of available data")
            break

        offset += CHUNK_SIZE
        chunk_num += 1

        # Safety limit - stop after 10 chunks (500k bars)
        if chunk_num > 10:
            print("[INFO] Reached safety limit (10 chunks)")
            break

    mt5.shutdown()

    if not all_data:
        print("\n[ERROR] No data downloaded")
        return

    # Combine all chunks
    print(f"\nCombining {len(all_data)} chunks...")
    combined_df = pd.concat(all_data, ignore_index=True)

    # Remove duplicates
    combined_df = combined_df.drop_duplicates(subset=['time'])

    # Sort by time
    combined_df = combined_df.sort_values('time')

    # Format columns
    combined_df = combined_df.rename(columns={
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'tick_volume': 'volume'
    })
    combined_df = combined_df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

    # Save to CSV
    output_file = os.path.join(OUTPUT_FOLDER, 'xauusd_M5.csv')
    combined_df.to_csv(output_file, index=False)

    file_size_mb = os.path.getsize(output_file) / (1024 * 1024)

    print("\n" + "=" * 60)
    print("  DOWNLOAD COMPLETE")
    print("=" * 60)
    print(f"\nTotal candles: {len(combined_df):,}")
    print(f"File size: {file_size_mb:.2f} MB")
    print(f"Date range: {combined_df['timestamp'].min()} to {combined_df['timestamp'].max()}")
    print(f"Saved: {output_file}")
    print("=" * 60 + "\n")

if __name__ == '__main__':
    main()

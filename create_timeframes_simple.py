"""
Simple script to create OHLCV timeframes from tick data.
Processes year by year to save memory.

Usage:
    python create_timeframes_simple.py [SYMBOL]

Default symbol is XAUUSD if not provided. Tick data is expected at
<script_dir>/<symbol_lower>/ticks/<year>/*.csv and OHLCV is written
to <script_dir>/data/.
"""

import pandas as pd
import os
import sys

# WHY (Phase 46 Fix 1): Old code used relative paths 'xauusd/ticks/'
#      and 'data/' which broke when run from any directory other than
#      the project root. Anchor to the script's own directory.
# CHANGED: April 2026 — Phase 46 Fix 1 — anchored paths
#          (audit Part D HIGH #49)
# WHY (Phase 46 Fix 2): SYMBOL was hardcoded XAUUSD. Accept it from
#      sys.argv[1] so the script works for any instrument with tick
#      data in <symbol_lower>/ticks/.
# CHANGED: April 2026 — Phase 46 Fix 2 — CLI symbol
#          (audit Part D HIGH #50)
_HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else 'XAUUSD'
TICK_DATA_PATH = os.path.join(_HERE, SYMBOL.lower(), 'ticks')
OUTPUT_PATH    = os.path.join(_HERE, 'data')

# Timeframes
TIMEFRAMES = {
    'M5': '5min',
    'M15': '15min',
    'H1': '1H',
    'H4': '4H',
    'D1': '1D',
    'W1': '1W',
    # WHY (Phase 46 Fix 3): '1ME' is pandas 2.x only and breaks
    #      pandas 1.x with KeyError at resample. '1M' works in both
    #      (deprecated in 2.x with warning, still functional).
    # CHANGED: April 2026 — Phase 46 Fix 3 — cross-version freq
    #          (audit Part D HIGH #51)
    'MN': '1M',
}

def process_year_to_timeframe(year, timeframe_name, resample_rule):
    """Process one year of tick data into a specific timeframe."""
    year_path = os.path.join(TICK_DATA_PATH, str(year))

    if not os.path.isdir(year_path):
        return None

    # Get all tick files for this year
    tick_files = sorted([f for f in os.listdir(year_path) if f.endswith('.csv')])

    if not tick_files:
        return None

    print(f"  Processing {year} ({len(tick_files)} files)...", flush=True)

    # Load all months for this year
    dfs = []
    for file in tick_files:
        file_path = os.path.join(year_path, file)
        df = pd.read_csv(file_path)
        # Handle malformed timestamps
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        df = df.dropna(subset=['timestamp'])
        dfs.append(df)

    # Combine all months
    year_ticks = pd.concat(dfs, ignore_index=True)
    # WHY (Phase 46 Fix 5): Overlapping tick files at month boundaries
    #      (e.g., last tick of January present in both Jan and Feb files)
    #      produced duplicate timestamps. Resampling then double-counted
    #      volume on the overlap candles.
    # CHANGED: April 2026 — Phase 46 Fix 5 — dedupe ticks
    #          (audit Part D HIGH #53)
    _before = len(year_ticks)
    year_ticks = year_ticks.drop_duplicates(subset=['timestamp'], keep='first')
    if len(year_ticks) < _before:
        print(f"  Dropped {_before - len(year_ticks)} duplicate ticks "
              f"({(_before - len(year_ticks)) / _before * 100:.1f}%)")
    year_ticks = year_ticks.sort_values('timestamp').set_index('timestamp')

    # WHY (Phase 46 Fix 4): Old code assumed a 'mid' column. Tick CSVs
    #      that only carry bid/ask crashed with KeyError. Fall back to
    #      (bid+ask)/2 if mid is absent.
    # CHANGED: April 2026 — Phase 46 Fix 4 — mid fallback
    #          (audit Part D HIGH #52)
    if 'mid' in year_ticks.columns:
        year_ticks['price'] = year_ticks['mid']
    elif 'bid' in year_ticks.columns and 'ask' in year_ticks.columns:
        year_ticks['price'] = (year_ticks['bid'] + year_ticks['ask']) / 2.0
    elif 'price' in year_ticks.columns:
        pass  # already named correctly
    else:
        print(f"  ERROR: tick data has no 'mid', 'bid'/'ask', or 'price' "
              f"columns. Available: {list(year_ticks.columns)}")
        return None

    # Resample to OHLCV
    ohlcv = pd.DataFrame()
    ohlcv['open'] = year_ticks['price'].resample(resample_rule).first()
    ohlcv['high'] = year_ticks['price'].resample(resample_rule).max()
    ohlcv['low'] = year_ticks['price'].resample(resample_rule).min()
    ohlcv['close'] = year_ticks['price'].resample(resample_rule).last()
    ohlcv['volume'] = year_ticks['volume'].resample(resample_rule).sum()

    ohlcv = ohlcv.reset_index()
    ohlcv = ohlcv.dropna()

    return ohlcv

def create_timeframe_file(timeframe_name, resample_rule):
    """Create complete OHLCV file for a timeframe."""
    print(f"\nCreating {timeframe_name} ({resample_rule})...", flush=True)

    # Get all year directories
    years = sorted([d for d in os.listdir(TICK_DATA_PATH)
                   if os.path.isdir(os.path.join(TICK_DATA_PATH, d)) and d.isdigit()])

    all_candles = []

    for year in years:
        candles = process_year_to_timeframe(year, timeframe_name, resample_rule)
        if candles is not None and len(candles) > 0:
            all_candles.append(candles)

    # Combine all years
    print(f"  Combining {len(all_candles)} years...", flush=True)
    combined = pd.concat(all_candles, ignore_index=True)
    combined = combined.sort_values('timestamp').reset_index(drop=True)

    # Save
    output_file = os.path.join(OUTPUT_PATH, f'{SYMBOL.lower()}_{timeframe_name}.csv')
    combined.to_csv(output_file, index=False)

    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print(f"  Saved {len(combined):,} candles ({size_mb:.1f} MB)", flush=True)
    print(f"  Date range: {combined['timestamp'].min()} to {combined['timestamp'].max()}", flush=True)

def main():
    print("=" * 60, flush=True)
    print("CREATING TIMEFRAME FILES", flush=True)
    print("=" * 60, flush=True)

    os.makedirs(OUTPUT_PATH, exist_ok=True)

    for tf_name, resample_rule in TIMEFRAMES.items():
        try:
            create_timeframe_file(tf_name, resample_rule)
        except Exception as e:
            print(f"ERROR processing {tf_name}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60, flush=True)
    print("DONE!", flush=True)
    print("=" * 60, flush=True)

if __name__ == '__main__':
    main()
